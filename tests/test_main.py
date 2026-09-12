"""API route tests.

The portal client dependency is overridden with a small fake — these tests verify the
REST layer's contract (status codes, response shapes, error mapping) independently of
the adapter's own logic, which is covered in test_client.py. No test here touches the
network or the real portal.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.client import PortalUnavailableError, SessionExpiredError
from app.main import app, get_client


class FakePortalClient:
    """Test double implementing the same surface as UrjaPortalClient."""

    def __init__(self, *, raise_on: Exception | None = None):
        self._raise_on = raise_on

    def _maybe_raise(self):
        if self._raise_on is not None:
            raise self._raise_on

    def login(self) -> bool:
        self._maybe_raise()
        return True

    def get_meters(self) -> list[dict]:
        self._maybe_raise()
        return [{"meter_id": "M-1", "serial_number": "SN-1", "status": "ACTIVE", "location": "Zone A"}]

    def get_meter_details(self, meter_id: str) -> dict:
        self._maybe_raise()
        return {
            "meter_id": meter_id,
            "serial_number": "SN-1",
            "status": "ACTIVE",
            "location": "Zone A",
            "installed_date": "2024-01-01",
            "raw_fields": {},
        }

    def get_meter_consumption(self, meter_id: str) -> list[dict]:
        self._maybe_raise()
        return [{"period": "2026-01", "value": 100.5, "unit": "kWh"}]

    def get_hierarchy(self) -> list[dict]:
        self._maybe_raise()
        return [{"node_id": "root", "name": "Root", "node_type": "region", "children": []}]


@pytest.fixture
def client_with_fake():
    def _make(fake):
        app.dependency_overrides[get_client] = lambda: fake
        yield TestClient(app)
        app.dependency_overrides.clear()

    return _make


def test_health_check():
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_root_redirects_to_docs():
    with TestClient(app, follow_redirects=False) as client:
        response = client.get("/")
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/docs"


def test_list_meters_returns_expected_shape(client_with_fake):
    for client in client_with_fake(FakePortalClient()):
        response = client.get("/api/v1/meters")

    assert response.status_code == 200
    body = response.json()
    assert body == [{"meter_id": "M-1", "serial_number": "SN-1", "status": "ACTIVE", "location": "Zone A"}]


def test_get_meter_detail_returns_expected_shape(client_with_fake):
    for client in client_with_fake(FakePortalClient()):
        response = client.get("/api/v1/meters/M-1")

    assert response.status_code == 200
    body = response.json()
    assert body["meter_id"] == "M-1"
    assert body["installed_date"] == "2024-01-01"


def test_get_meter_consumption_returns_records(client_with_fake):
    for client in client_with_fake(FakePortalClient()):
        response = client.get("/api/v1/meters/M-1/consumption")

    assert response.status_code == 200
    body = response.json()
    assert body["meter_id"] == "M-1"
    assert body["records"][0]["value"] == 100.5


def test_get_hierarchy_returns_tree(client_with_fake):
    for client in client_with_fake(FakePortalClient()):
        response = client.get("/api/v1/hierarchy")

    assert response.status_code == 200
    body = response.json()
    assert body[0]["node_id"] == "root"


def test_login_endpoint_reports_success(client_with_fake):
    for client in client_with_fake(FakePortalClient()):
        response = client.post("/api/v1/auth/login")

    assert response.status_code == 200
    assert response.json()["authenticated"] is True


def test_session_expired_maps_to_401(client_with_fake):
    for client in client_with_fake(FakePortalClient(raise_on=SessionExpiredError("no session"))):
        response = client.get("/api/v1/meters")

    assert response.status_code == 401
    assert "no session" in response.json()["detail"]


def test_portal_unavailable_maps_to_502(client_with_fake):
    for client in client_with_fake(FakePortalClient(raise_on=PortalUnavailableError("portal is down"))):
        response = client.get("/api/v1/meters/M-1")

    assert response.status_code == 502
    assert "portal is down" in response.json()["detail"]
