"""Unit tests for the UrjaPortalClient adapter.

Everything here runs against httpx.MockTransport — no test ever touches the real
portal. The mock transport IS the external boundary; nothing inside the client is
mocked, so these tests exercise the real login/re-auth/parsing logic.
"""

from __future__ import annotations

import httpx
import pytest

from app.client import (
    PortalUnavailableError,
    SessionExpiredError,
    UrjaPortalClient,
    normalize_number,
    normalize_whitespace,
)

LOGIN_FORM_HTML = """
<html><body><form method="POST"><input name="email"/><input name="password"/></form></body></html>
"""

METER_DETAIL_HTML = """
<html><body>
  <span id="meter-serial">SN-778812</span>
  <td class="status-value">Active</td>
</body></html>
"""

METERS_TABLE_HTML = """
<html><body>
<table>
  <tr><th>Meter ID</th><th>Serial</th><th>Status</th></tr>
  <tr><td>M-001</td><td>SN-100</td><td>Active</td></tr>
  <tr><td>M-002</td><td>SN-200</td><td>Fault</td></tr>
</table>
</body></html>
"""

CONSUMPTION_TABLE_HTML = """
<html><body>
<table>
  <tr><th>Period</th><th>Value</th></tr>
  <tr><td>2026-01</td><td>1,234.50 kWh</td></tr>
  <tr><td>2026-02</td><td>987.25 kWh</td></tr>
</table>
</body></html>
"""


def make_client(handler) -> UrjaPortalClient:
    """Build a client whose httpx transport is a deterministic mock handler."""
    client = UrjaPortalClient(base_url="https://urja-ops.flockenergy.tech", email="operator@urja.local", password="urja-ops-2026")
    client._client = httpx.Client(
        base_url=client.base_url,
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
        headers={"Origin": client.base_url},
    )
    return client


# -- normalize_whitespace / normalize_number ------------------------------------------


def test_normalize_whitespace_collapses_and_strips():
    assert normalize_whitespace("  Active \n  Meter  ") == "Active Meter"


def test_normalize_whitespace_handles_none():
    assert normalize_whitespace(None) == ""


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("124.50", 124.50),
        ("1,234.50 kWh", 1234.50),
        ("-42", -42.0),
        ("", None),
        ("N/A", None),
        (None, None),
    ],
)
def test_normalize_number(raw, expected):
    assert normalize_number(raw) == expected


# -- login ------------------------------------------------------------------------------


def test_login_success_sets_authenticated_true():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login":
            assert b"email=operator%40urja.local" in request.content
            return httpx.Response(303, headers={"location": "/dashboard", "set-cookie": "session=abc123; HttpOnly"})
        # httpx follows the 303 redirect automatically (follow_redirects=True) — the
        # dashboard landing page itself is just a plain authenticated 200.
        assert request.url.path == "/dashboard"
        return httpx.Response(200, html="<html><body>Dashboard</body></html>")

    client = make_client(handler)
    result = client.login()

    assert result is True
    assert client._authenticated is True


def test_login_failure_stays_on_login_page():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, html=LOGIN_FORM_HTML)  # re-rendered login form, no cookie

    client = make_client(handler)
    result = client.login()

    assert result is False
    assert client._authenticated is False


def test_login_raises_portal_unavailable_on_5xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    client = make_client(handler)
    with pytest.raises(PortalUnavailableError):
        client.login()


# -- get_meters ---------------------------------------------------------------------------


def test_get_meters_uses_json_api_when_available():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login":
            return httpx.Response(303, headers={"location": "/dashboard", "set-cookie": "session=abc"})
        if request.url.path == "/api/v1/meters":
            return httpx.Response(
                200,
                json=[{"meter_id": "M-1", "serial_number": "SN-1", "status": "active", "region": "north"}],
            )
        return httpx.Response(404)

    client = make_client(handler)
    meters = client.get_meters()

    assert len(meters) == 1
    assert meters[0]["meter_id"] == "M-1"
    assert meters[0]["status"] == "ACTIVE"  # normalised to upper case
    assert meters[0]["raw_fields"]["region"] == "north"  # unmapped field preserved


def test_get_meters_falls_back_to_html_table_when_no_json():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login":
            return httpx.Response(303, headers={"location": "/dashboard", "set-cookie": "session=abc"})
        if request.url.path in ("/api/v1/meters", "/meters") and "application/json" in request.headers.get("accept", ""):
            return httpx.Response(404)  # no JSON API here
        if request.url.path == "/meters":
            return httpx.Response(200, html=METERS_TABLE_HTML)
        return httpx.Response(404)

    client = make_client(handler)
    meters = client.get_meters()

    assert len(meters) == 2
    assert meters[0] == {"meter_id": "M-001", "serial_number": "SN-100", "status": "ACTIVE", "location": None}
    assert meters[1]["status"] == "FAULT"


# -- get_meter_details ----------------------------------------------------------------------


def test_get_meter_details_html_fallback_uses_brief_example_selectors():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login":
            return httpx.Response(303, headers={"location": "/dashboard", "set-cookie": "session=abc"})
        if "application/json" in request.headers.get("accept", ""):
            return httpx.Response(404)
        if request.url.path == "/meters/M-1":
            return httpx.Response(200, html=METER_DETAIL_HTML)
        return httpx.Response(404)

    client = make_client(handler)
    detail = client.get_meter_details("M-1")

    assert detail["meter_id"] == "M-1"
    assert detail["serial_number"] == "SN-778812"
    assert detail["status"] == "ACTIVE"


def test_get_meter_details_reports_unknown_when_elements_missing():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login":
            return httpx.Response(303, headers={"location": "/dashboard", "set-cookie": "session=abc"})
        if "application/json" in request.headers.get("accept", ""):
            return httpx.Response(404)
        return httpx.Response(200, html="<html><body>no data here</body></html>")

    client = make_client(handler)
    detail = client.get_meter_details("M-2")

    assert detail["serial_number"] == "N/A"
    assert detail["status"] == "UNKNOWN"


# -- consumption -------------------------------------------------------------------------


def test_get_meter_consumption_html_fallback_normalizes_values():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login":
            return httpx.Response(303, headers={"location": "/dashboard", "set-cookie": "session=abc"})
        if "application/json" in request.headers.get("accept", ""):
            return httpx.Response(404)
        return httpx.Response(200, html=CONSUMPTION_TABLE_HTML)

    client = make_client(handler)
    records = client.get_meter_consumption("M-1")

    assert records[0] == {"period": "2026-01", "value": 1234.50, "unit": None}
    assert records[1]["value"] == 987.25


# -- auto re-authentication -----------------------------------------------------------------


def test_auto_reauth_retries_once_after_session_expiry():
    login_calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login":
            login_calls["count"] += 1
            return httpx.Response(303, headers={"location": "/dashboard", "set-cookie": f"session=tok{login_calls['count']}"})
        if request.url.path == "/api/v1/meters":
            # First attempt after the initial login looks expired; the retry succeeds.
            if login_calls["count"] == 1:
                return httpx.Response(401)
            return httpx.Response(200, json=[{"meter_id": "M-1", "serial_number": "SN-1", "status": "ACTIVE"}])
        return httpx.Response(404)

    client = make_client(handler)
    meters = client.get_meters()

    assert login_calls["count"] == 2  # initial login + one re-login after the 401
    assert meters[0]["meter_id"] == "M-1"


def test_session_expired_error_when_relogin_also_fails():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login":
            return httpx.Response(200, html=LOGIN_FORM_HTML)  # every login attempt fails
        return httpx.Response(401)

    client = make_client(handler)
    with pytest.raises(SessionExpiredError):
        client.get_meters()
