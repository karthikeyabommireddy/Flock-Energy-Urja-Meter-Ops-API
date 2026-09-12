"""Legacy Urja Portal HTTP adapter.

Responsible purely for talking to the Urja Meter Ops portal: authenticating, keeping a
session alive, detecting when it has expired, and normalising whatever the portal sends
back (JSON or HTML) into plain dictionaries. `app/main.py` maps those dictionaries onto
the API's own response models — this module never imports FastAPI or knows about the
REST layer at all.

See PROTOCOL.md for what was verified about the portal and what was not. Two
consequences of that document drive this file's design:

1. The login form's field names are `email` / `password` (verified), posted as
   `application/x-www-form-urlencoded` to `/login` itself — SvelteKit's default CSRF
   protection checks the `Origin` header on non-GET requests, so every mutating
   request sends an explicit `Origin` header matching the portal's own origin.
2. Whether authenticated data is served as JSON or as server-rendered HTML was not
   confirmed (recon was blocked before authentication — see PROTOCOL.md §5). Every
   data-fetching method here therefore PROBES a JSON endpoint first and FALLS BACK to
   HTML parsing — see `_fetch_json_or_html()`.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("urja_client")

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_whitespace(text: str | None) -> str:
    """Collapse runs of whitespace/newlines from scraped HTML into single spaces."""
    if text is None:
        return ""
    return _WHITESPACE_RE.sub(" ", text).strip()


def normalize_number(text: str | None) -> float | None:
    """Convert a scraped numeric string (e.g. '1,234.50 kWh') to a float, or None."""
    if text is None:
        return None
    cleaned = normalize_whitespace(text)
    cleaned = re.sub(r"[^\d.\-]", "", cleaned)
    if not cleaned or cleaned in {"-", "."}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


class SessionExpiredError(Exception):
    """Raised when a request fails auth and the automatic re-login also fails."""


class PortalUnavailableError(Exception):
    """Raised when the portal is unreachable or returns an unexpected server error."""


class UrjaPortalClient:
    """Adapter over the legacy Urja Meter Ops portal.

    One instance holds one authenticated session (via httpx's cookie jar). Safe to
    reuse across requests in a single-process deployment; see `app/main.py` for how
    it's wired as a FastAPI dependency singleton.
    """

    #: Candidate JSON API paths tried, in order, before falling back to HTML parsing.
    #: `{path}` templates are formatted with the resource's own path segment.
    _JSON_CANDIDATES = ("/api/v1{path}", "{path}")

    def __init__(self, base_url: str, email: str, password: str, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._email = email
        self._password = password
        self._authenticated = False
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            follow_redirects=True,
            headers={"Origin": self.base_url},
        )

    def close(self) -> None:
        self._client.close()

    # -- Authentication -----------------------------------------------------------

    def login(self) -> bool:
        """POST the login form. Returns True on success, False on rejected credentials.

        Raises PortalUnavailableError if the portal itself is unreachable or errors.
        """
        try:
            response = self._client.post(
                "/login",
                data={"email": self._email, "password": self._password},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except httpx.HTTPError as exc:
            raise PortalUnavailableError(f"login request failed: {exc}") from exc

        if response.status_code >= 500:
            raise PortalUnavailableError(f"portal returned {response.status_code} on login")

        # SvelteKit form actions typically respond to a successful login with a
        # redirect away from /login (httpx already followed it, so the final URL is
        # what tells us). A failed login re-renders /login itself, still with the
        # form present — status 200 either way (see PROTOCOL.md §3).
        landed_on_login = response.url.path.rstrip("/").endswith("/login")
        got_session_cookie = len(self._client.cookies) > 0

        self._authenticated = (not landed_on_login) and got_session_cookie
        if not self._authenticated:
            logger.warning("login did not establish a session (landed_on_login=%s, cookie=%s)",
                            landed_on_login, got_session_cookie)
        return self._authenticated

    def _ensure_authenticated(self) -> None:
        if not self._authenticated:
            if not self.login():
                raise SessionExpiredError("could not authenticate with the portal")

    def _looks_like_login_redirect(self, response: httpx.Response) -> bool:
        return response.status_code == 401 or response.url.path.rstrip("/").endswith("/login")

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Authenticated request with one automatic re-login retry on session expiry."""
        self._ensure_authenticated()
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise PortalUnavailableError(f"{method} {path} failed: {exc}") from exc

        if self._looks_like_login_redirect(response):
            logger.info("session appears expired on %s %s — re-authenticating", method, path)
            self._authenticated = False
            if not self.login():
                raise SessionExpiredError("session expired and re-login failed")
            try:
                response = self._client.request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                raise PortalUnavailableError(f"{method} {path} failed after re-login: {exc}") from exc

        if response.status_code >= 500:
            raise PortalUnavailableError(f"portal returned {response.status_code} for {method} {path}")

        return response

    # -- JSON-first, HTML-fallback data fetching -----------------------------------

    def _fetch_json_or_html(self, path: str) -> tuple[Any | None, BeautifulSoup | None]:
        """Try JSON API candidates for `path`, then fall back to parsing it as HTML.

        Returns (json_data, None) if a candidate returned JSON, or (None, soup) if
        falling back to the HTML page at `path` itself. Raises if neither works.
        """
        for template in self._JSON_CANDIDATES:
            candidate = template.format(path=path)
            response = self._request("GET", candidate, headers={"Accept": "application/json"})
            content_type = response.headers.get("content-type", "")
            if "application/json" in content_type:
                try:
                    return response.json(), None
                except ValueError:
                    continue  # claimed JSON but wasn't parseable — try the next candidate

        # Nothing returned real JSON — fetch the page itself and parse it as HTML.
        response = self._request("GET", path)
        return None, BeautifulSoup(response.text, "html.parser")

    # -- Public data methods --------------------------------------------------------

    def get_meters(self) -> list[dict[str, Any]]:
        """List of meters — GET /meters (JSON-first, HTML-table fallback)."""
        data, soup = self._fetch_json_or_html("/meters")
        if data is not None:
            items = data if isinstance(data, list) else data.get("meters", data.get("items", []))
            return [self._normalize_meter_json(item) for item in items]

        return self._parse_meter_table(soup)

    def get_meter_details(self, meter_id: str) -> dict[str, Any]:
        """Single meter detail — GET /meters/{id} (JSON-first, HTML fallback)."""
        data, soup = self._fetch_json_or_html(f"/meters/{meter_id}")
        if data is not None:
            return self._normalize_meter_json(data, meter_id=meter_id)

        # HTML fallback: element ids/classes per the assignment brief's own example
        # (#meter-serial, .status-value) — the closest available hint since live
        # recon of an authenticated page was blocked. See PROTOCOL.md §4-5.
        serial_elem = soup.find(id="meter-serial") or soup.find(attrs={"class": "meter-serial"})
        status_elem = soup.find(class_="status-value") or soup.find(id="status-value")
        location_elem = soup.find(class_="location-value") or soup.find(id="meter-location")
        installed_elem = soup.find(class_="installed-date") or soup.find(id="installed-date")

        return {
            "meter_id": meter_id,
            "serial_number": normalize_whitespace(serial_elem.get_text()) if serial_elem else "N/A",
            "status": normalize_whitespace(status_elem.get_text()).upper() if status_elem else "UNKNOWN",
            "location": normalize_whitespace(location_elem.get_text()) if location_elem else None,
            "installed_date": normalize_whitespace(installed_elem.get_text()) if installed_elem else None,
            "raw_fields": {},
        }

    def get_meter_consumption(self, meter_id: str) -> list[dict[str, Any]]:
        """Consumption history — GET /meters/{id}/consumption (JSON-first, HTML fallback)."""
        data, soup = self._fetch_json_or_html(f"/meters/{meter_id}/consumption")
        if data is not None:
            items = data if isinstance(data, list) else data.get("records", data.get("consumption", []))
            return [
                {
                    "period": str(item.get("period") or item.get("date") or item.get("timestamp") or ""),
                    "value": normalize_number(str(item.get("value", item.get("kwh", "")))),
                    "unit": item.get("unit"),
                }
                for item in items
            ]

        return self._parse_consumption_table(soup)

    def get_hierarchy(self) -> list[dict[str, Any]]:
        """Network hierarchy tree — GET /hierarchy (optional extension)."""
        data, soup = self._fetch_json_or_html("/hierarchy")
        if data is not None:
            items = data if isinstance(data, list) else data.get("hierarchy", data.get("nodes", []))
            return items

        return self._parse_hierarchy_tree(soup)

    # -- HTML parsing helpers -------------------------------------------------------

    @staticmethod
    def _normalize_meter_json(item: dict[str, Any], meter_id: str | None = None) -> dict[str, Any]:
        known = {"meter_id", "serial_number", "status", "location", "installed_date"}
        return {
            "meter_id": str(item.get("meter_id") or item.get("id") or meter_id or ""),
            "serial_number": str(item.get("serial_number") or item.get("serial") or "N/A"),
            "status": str(item.get("status", "UNKNOWN")).upper(),
            "location": item.get("location"),
            "installed_date": item.get("installed_date") or item.get("installedAt"),
            "raw_fields": {k: str(v) for k, v in item.items() if k not in known},
        }

    @staticmethod
    def _parse_meter_table(soup: BeautifulSoup) -> list[dict[str, Any]]:
        """Best-effort parse of an HTML `<table>` listing meters into structured rows.

        Looks for the first table on the page, reads its header row for column names,
        and maps rows positionally. Unverified against a live page — see PROTOCOL.md.
        """
        table = soup.find("table")
        if table is None:
            return []

        headers = [normalize_whitespace(th.get_text()).lower() for th in table.find_all("th")]
        rows: list[dict[str, Any]] = []
        for tr in table.find_all("tr")[1:] if headers else table.find_all("tr"):
            cells = [normalize_whitespace(td.get_text()) for td in tr.find_all("td")]
            if not cells:
                continue
            record = dict(zip(headers, cells)) if headers else {}
            rows.append(
                {
                    "meter_id": record.get("meter id") or record.get("id") or (cells[0] if cells else ""),
                    "serial_number": record.get("serial") or record.get("serial number") or (cells[1] if len(cells) > 1 else "N/A"),
                    "status": (record.get("status") or (cells[2] if len(cells) > 2 else "UNKNOWN")).upper(),
                    "location": record.get("location"),
                }
            )
        return rows

    @staticmethod
    def _parse_consumption_table(soup: BeautifulSoup) -> list[dict[str, Any]]:
        table = soup.find("table")
        if table is None:
            return []
        rows: list[dict[str, Any]] = []
        for tr in table.find_all("tr")[1:]:
            cells = [normalize_whitespace(td.get_text()) for td in tr.find_all("td")]
            if len(cells) < 2:
                continue
            rows.append({"period": cells[0], "value": normalize_number(cells[1]), "unit": cells[2] if len(cells) > 2 else None})
        return rows

    @staticmethod
    def _parse_hierarchy_tree(soup: BeautifulSoup) -> list[dict[str, Any]]:
        """Best-effort parse of a nested `<ul>/<li>` hierarchy tree."""
        root = soup.find("ul")
        if root is None:
            return []

        def parse_list(ul) -> list[dict[str, Any]]:
            nodes = []
            for li in ul.find_all("li", recursive=False):
                nested = li.find("ul")
                label = normalize_whitespace(li.contents[0]) if li.contents else normalize_whitespace(li.get_text())
                nodes.append(
                    {
                        "node_id": label.lower().replace(" ", "-"),
                        "name": label,
                        "node_type": None,
                        "children": parse_list(nested) if nested else [],
                    }
                )
            return nodes

        return parse_list(root)
