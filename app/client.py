"""Legacy Urja Portal HTTP adapter.

Responsible purely for talking to the Urja Meter Ops portal: authenticating, keeping a
session alive, detecting when it has expired, and normalising whatever the portal sends
back into plain dictionaries. `app/main.py` maps those dictionaries onto the API's own
response models — this module never imports FastAPI or knows about the REST layer at all.

PROTOCOL.md records what was observed. Three facts drive this file's design:

1. The login form's fields are `email` / `password`, posted as
   `application/x-www-form-urlencoded` to `/login` itself — SvelteKit's default CSRF
   protection checks the `Origin` header on such posts, so every request sends an explicit
   `Origin` header matching the portal's own origin. The UI posts with
   `x-sveltekit-action: true`, which makes SvelteKit answer in JSON
   (`{"type": "redirect", "location": "/meters"}` on success), and so does this adapter:
   success is stated by the portal rather than inferred from where we landed.
2. The portal is a SvelteKit app with a JSON API behind it: `/portal/meters/search` (the
   bulk path: 20 meters a page, with `total`), `/portal/meters/<id>/geo`,
   `/portal/meters/<id>/energy` and `/portal/dts`. A meter's nameplate and its place in
   the network come from the page's own data endpoint, `/meters/<id>/__data.json`. This
   module calls only what the UI itself calls: no guessed paths, no HTML scraping.
3. An expired session redirects to `/login`, and the portal now and then answers a
   transient `502 Bad Gateway`.

Concurrency and failure handling (v1.1). FastAPI runs the sync routes in a thread pool,
so one client instance is shared by many threads:

- Re-login is single-flight. A lock plus a session "generation" counter mean that N
  threads hitting an expired session cause exactly ONE login: the first thread logs in
  and bumps the generation, the others see it changed and just retry with the new cookie.
- After the portal rejects our credentials, further logins are refused for a cool-down that
  starts at `_AUTH_COOLDOWN_SECONDS` and doubles with every consecutive rejection (up to 15
  minutes), so a typo in `.env` is retried ever more rarely instead of hammering the account.
  An explicit `login()` within `_LOGIN_REUSE_SECONDS` of a successful one reuses that session
  rather than posting the password again.
- Outbound calls are capped by a semaphore (`max_concurrency`), and transient failures
  (timeouts, connection errors, 429/5xx) are retried a bounded number of times with
  jittered back-off before surfacing as `PortalOutageError` (503) or `PortalTimeoutError`
  (504). Both subclass `PortalUnavailableError`, so existing handlers keep working.
- Only GET requests and the single login POST ever leave this module. A request hook checks
  every hop, redirects included, *before* it is sent and refuses any that would leave the
  portal's origin, so the login form's password cannot follow a redirect elsewhere.
"""

from __future__ import annotations

import logging
import math
import random
import re
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from typing import Any, TypeVar
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from app.errors import PortalNotFoundError, PortalParseError
from app.hierarchy import build_hierarchy
from app.parsers import (
    parse_dt_page,
    parse_energy,
    parse_geo,
    parse_meter_detail,
    parse_meter_page,
)

logger = logging.getLogger("urja_client")

T = TypeVar("T")

_WHITESPACE_RE = re.compile(r"\s+")

#: Statuses worth retrying: the portal (or something in front of it) is overloaded or restarting.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_RETRY_AFTER_SECONDS = 10.0
#: After the portal rejects our credentials, wait this long before trying again: repeated
#: bad logins are exactly what triggers account lockouts. Each further consecutive rejection
#: doubles the wait, up to the maximum.
_AUTH_COOLDOWN_SECONDS = 30.0
_AUTH_COOLDOWN_MAX_SECONDS = 900.0
#: An explicit `login()` this soon after a successful one reuses that session instead of posting the password again.
_LOGIN_REUSE_SECONDS = 30.0
_USER_AGENT = "flock-urja-api/1.1 (read-only integration)"
#: Hard stop for a paged endpoint whose `total` makes no sense.
_MAX_PAGES = 1000
#: A hierarchy report is a crawl of one request per transformer; callers within this window share it.
_REPORT_MEMO_SECONDS = 30.0
#: What the UI requests right after login and for each meter page: SvelteKit's "which nodes to load" flags.
_LAYOUT_NODE = {"x-sveltekit-invalidated": "010"}
_PAGE_NODE = {"x-sveltekit-invalidated": "001"}


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


class PortalOutageError(PortalUnavailableError):
    """Connection refused/reset, or a 429/5xx that survived every retry (surfaces as HTTP 503)."""


class PortalTimeoutError(PortalUnavailableError):
    """The portal did not answer within the timeout, even after retries (surfaces as HTTP 504)."""


def _backoff(attempt: int) -> float:
    """Jittered exponential back-off: roughly 0.25s, 0.5s, 1s ... capped at 8s."""
    return float(min(8.0, 0.25 * 2**attempt)) * random.uniform(0.5, 1.0)


def _retry_after(response: httpx.Response) -> float | None:
    """Seconds the portal asked us to wait (`Retry-After: <seconds>`), capped; None if absent or not a number."""
    try:
        seconds = float(response.headers.get("retry-after", ""))
    except ValueError:
        return None  # an HTTP date, or junk: the caller falls back to its own back-off
    return min(max(seconds, 0.0), _MAX_RETRY_AFTER_SECONDS) if math.isfinite(seconds) else None


class UrjaPortalClient:
    """Adapter over the legacy Urja Meter Ops portal.

    One instance holds one authenticated session (via httpx's cookie jar) and is safe to
    share between threads: see `app/main.py` for how it's wired as a FastAPI dependency
    singleton, and the module docstring for how concurrent session expiry and transient
    failures are handled.

    `timezone` is the IANA zone the portal's naive timestamps are read in. The portal states
    none (PROTOCOL.md), so the default, Asia/Kolkata, is an assumption: the network is in Jaipur.
    """

    def __init__(
        self,
        base_url: str,
        email: str,
        password: str,
        timeout: float = 10.0,
        max_retries: int = 2,
        max_concurrency: int = 4,
        timezone: str = "Asia/Kolkata",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._email = email
        self._password = password
        self._authenticated = False
        origin = httpx.URL(self.base_url)
        self._host = origin.host
        self._origin = (origin.scheme, origin.host, origin.port)  # what every request, redirects included, must match
        self._max_retries = max(0, max_retries)
        self._max_concurrency = max(1, max_concurrency)
        self._timezone_name = timezone
        self._tz: ZoneInfo | None = None  # resolved on first use: only consumption needs the zone database
        self._lock = threading.Lock()  # one thread at a time talks to /login
        self._session_generation = 0  # bumped on every successful login
        self._auth_failed_at: float | None = None  # monotonic time of the last rejected login
        self._auth_failures = 0  # consecutive rejected logins; drives the growing cool-down
        self._logged_in_at = float("-inf")  # monotonic time of the last successful login
        self.last_login_error: str | None = None  # why the last login did not authenticate
        self._slots = threading.BoundedSemaphore(self._max_concurrency)  # cap on in-flight portal calls
        self._report_lock = threading.Lock()
        self._report_memo: tuple[float, dict[str, Any]] | None = None
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout, connect=min(5.0, timeout)),
            limits=httpx.Limits(
                max_connections=self._max_concurrency,
                max_keepalive_connections=self._max_concurrency,
            ),
            follow_redirects=True,
            headers={"Origin": self.base_url, "User-Agent": _USER_AGENT},
            event_hooks={"request": [self._refuse_other_origin]},
        )

    def close(self) -> None:
        self._client.close()

    def _refuse_other_origin(self, request: httpx.Request) -> None:
        """Runs before every hop, redirects included: nothing, least of all the login post, may leave the origin."""
        url = request.url
        if (url.scheme, url.host, url.port) != self._origin:
            raise PortalUnavailableError(f"refusing to send {request.method} {url.path} to a different origin")

    def ping(self) -> None:
        """A cheap round trip that needs a live session: the user node the UI loads after login."""
        self._get_listing("/meters/__data.json", params=_LAYOUT_NODE)

    # -- Authentication -----------------------------------------------------------

    def login(self) -> bool:
        """POST the login form. Returns True on success, False on rejected credentials.

        Raises PortalUnavailableError if the portal itself is unreachable or errors.
        Callers take turns (one login at a time). A session established within the last
        `_LOGIN_REUSE_SECONDS` is reused, so this cannot be used to make the portal see the
        password over and over. After a rejected login further attempts are refused for a
        cool-down that grows with each consecutive rejection; `last_login_error` says why.
        """
        with self._lock:
            if self._authenticated and time.monotonic() - self._logged_in_at < _LOGIN_REUSE_SECONDS:
                return True
            return self._login_locked()

    def _cooldown_seconds(self) -> float:
        """30 s after the first rejection, doubling with each consecutive one, up to 15 minutes."""
        doublings = min(max(self._auth_failures - 1, 0), 10)
        return min(_AUTH_COOLDOWN_MAX_SECONDS, _AUTH_COOLDOWN_SECONDS * 2**doublings)

    def _login_locked(self) -> bool:
        """The login itself. The caller must hold `self._lock`."""
        if self._auth_failed_at is not None and time.monotonic() - self._auth_failed_at < self._cooldown_seconds():
            self.last_login_error = "cooldown"
            logger.warning("login skipped: the portal refused our login moments ago")
            return False

        response = self._send(
            "POST",
            "/login",
            data={"email": self._email, "password": self._password},
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                # What the UI sends: SvelteKit then answers the form post in JSON instead of redirecting.
                "Accept": "application/json",
                "x-sveltekit-action": "true",
            },
        )

        if response.status_code >= 500:
            raise PortalUnavailableError(f"portal returned {response.status_code} on login")

        # A plain SvelteKit form post answers success with a redirect away from /login (httpx
        # already followed it, so the final URL is what tells us) and failure by re-rendering
        # /login itself — status 200 either way (see PROTOCOL.md §3). With the action header
        # the portal states the verdict itself, and that takes precedence.
        landed_on_login = response.url.path.rstrip("/").endswith("/login")
        action = self._action_result(response)
        if action is not None:
            landed_on_login = action.get("type") != "redirect" or "/login" in str(action.get("location", ""))
        got_session_cookie = len(self._client.cookies) > 0

        self._authenticated = (not landed_on_login) and got_session_cookie
        if self._authenticated:
            self._session_generation += 1
            self._auth_failed_at = None
            self._auth_failures = 0
            self._logged_in_at = time.monotonic()
            self.last_login_error = None
            return True

        self._auth_failed_at = time.monotonic()
        self._auth_failures += 1
        # A 403 on a form POST is SvelteKit's CSRF/Origin check refusing us, not a wrong password.
        self.last_login_error = "csrf_origin" if response.status_code == 403 else "rejected"
        logger.warning(
            "login did not establish a session (status=%s, result=%s, landed_on_login=%s, cookie=%s)",
            response.status_code,
            action.get("type") if action else None,
            landed_on_login,
            got_session_cookie,
        )
        return False

    @staticmethod
    def _action_result(response: httpx.Response) -> dict[str, Any] | None:
        """SvelteKit's form-action result (`{"type": "redirect" | "failure" | ...}`), or None if this isn't one."""
        if "application/json" not in response.headers.get("content-type", ""):
            return None
        try:
            body = response.json()
        except ValueError:
            return None
        return body if isinstance(body, dict) and "type" in body else None

    def _ensure_authenticated(self) -> int:
        """Log in if there is no session yet. Returns the session generation now in force."""
        if not self._authenticated:
            with self._lock:
                # Another thread may have logged in while we waited for the lock.
                if not self._authenticated and not self._login_locked():
                    raise SessionExpiredError(f"could not authenticate with the portal ({self.last_login_error})")
        return self._session_generation

    def _relogin(self, seen_generation: int) -> None:
        """Re-authenticate after the portal turned us away: once, however many threads noticed."""
        with self._lock:
            if self._session_generation != seen_generation:
                return  # another thread already refreshed the session while we waited for the lock
            self._authenticated = False
            if not self._login_locked():
                raise SessionExpiredError(f"session expired and re-login failed ({self.last_login_error})")

    def _looks_like_login_redirect(self, response: httpx.Response) -> bool:
        if response.status_code == 401 or response.url.path.rstrip("/").endswith("/login"):
            return True
        # SvelteKit data requests (`__data.json`) express a redirect as JSON rather than as a 3xx.
        head = response.content[:200]
        return head.startswith(b'{"type":"redirect"') and b"/login" in head

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Authenticated request: lazy login, transient retries, one re-login on session expiry."""
        if not path.startswith("/") or path.startswith("//"):
            # Paths are built from validated, URL-encoded ids; anything else is a bug or an SSRF attempt.
            raise ValueError(f"portal paths must start with a single '/', got {path!r}")
        generation = self._ensure_authenticated()
        response = self._send(method, path, **kwargs)

        if self._looks_like_login_redirect(response):
            logger.info("session appears expired on %s %s — re-authenticating", method, path)
            self._relogin(generation)
            response = self._send(method, path, **kwargs)
            if self._looks_like_login_redirect(response):
                raise SessionExpiredError("still logged out immediately after re-authenticating")

        if response.status_code >= 500:
            raise PortalUnavailableError(f"portal returned {response.status_code} for {method} {path}")

        return response

    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """One HTTP exchange, capped by the concurrency semaphore, with bounded jittered retries.

        Retries timeouts, connection errors and 429/5xx. It never retries a 401 or a redirect
        to the login page (that is session expiry, handled by `_request`) or any other 4xx.
        """
        label = f"{method} {path}"
        for attempt in range(self._max_retries + 1):
            is_last = attempt == self._max_retries
            try:
                with self._slots:
                    response = self._client.request(method, path, **kwargs)
            except httpx.TimeoutException as exc:
                if is_last:
                    raise PortalTimeoutError(f"{label} timed out") from exc
                reason, delay = "timed out", _backoff(attempt)
            except httpx.TooManyRedirects as exc:
                raise PortalUnavailableError(f"{label} redirected too many times") from exc
            except httpx.HTTPError as exc:  # connection refused/reset, network and protocol errors
                reason = f"{type(exc).__name__}: {exc}"
                if is_last:
                    # The details (resolver, TLS, socket) go to the log; an API caller is told only the kind of failure.
                    logger.warning("portal request %s failed: %s", label, reason)
                    raise PortalOutageError(f"{label} failed ({type(exc).__name__})") from exc
                delay = _backoff(attempt)
            else:
                if response.url.host != self._host:  # the request hook refuses this first; this covers a swapped client
                    raise PortalUnavailableError(f"{label} was redirected to an unexpected host")
                if response.status_code not in _RETRYABLE_STATUS:
                    return response
                if is_last:
                    raise PortalOutageError(f"portal returned {response.status_code} for {label}")
                reason = f"HTTP {response.status_code}"
                retry_after = _retry_after(response)
                delay = retry_after if retry_after is not None else _backoff(attempt)
            logger.warning(
                "portal request %s failed (%s; attempt %d of %d), retrying in %.2fs",
                label,
                reason,
                attempt + 1,
                self._max_retries + 1,
                delay,
            )
            time.sleep(delay)
        raise AssertionError("unreachable")

    # -- JSON endpoints --------------------------------------------------------------

    def _get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET a portal JSON endpoint (authenticated, retried) and decode it.

        A 404 is PortalNotFoundError. Any other non-2xx, or a body that is not JSON, is an upstream
        problem: an HTML body here usually means the portal served a login or maintenance page.
        Query parameters go in `params`, never into the path, so characters such as the `&` in
        serial numbers and in the make "L&T" are percent-encoded instead of splitting the query.
        """
        response = self._request("GET", path, params=params)
        if response.status_code == 404:
            raise PortalNotFoundError(f"GET {path} was not found")
        if response.status_code >= 400:
            raise PortalUnavailableError(f"portal returned {response.status_code} for GET {path}")
        content_type = response.headers.get("content-type", "")
        if "json" not in content_type:
            raise PortalParseError(f"GET {path} did not return JSON (content-type {content_type[:60]!r})")
        try:
            return response.json()
        except ValueError as exc:
            raise PortalParseError(f"GET {path} returned invalid JSON") from exc

    def _get_listing(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """`_get_json` for an endpoint that has to exist: a 404 means the portal changed, not that a record is missing.

        Reported as the portal being unavailable (502), which, unlike a 404, lets the cache serve its last good answer.
        """
        try:
            return self._get_json(path, params=params)
        except PortalNotFoundError as exc:
            raise PortalUnavailableError(f"{path} is missing on the portal (HTTP 404)") from exc

    def _map_concurrent(self, fn: Callable[[Any], T], items: Iterable[Any]) -> list[T]:
        """`fn` over `items` on a small thread pool, results in order.

        The semaphore in `_send` still caps what the portal sees. As soon as any call fails, whichever
        finished first, the queued rest is dropped instead of hammering a portal that is already in
        trouble (calls already in flight run to the end).
        """
        work = list(items)
        if len(work) <= 1:
            return [fn(item) for item in work]
        pool = ThreadPoolExecutor(max_workers=min(self._max_concurrency, len(work)))
        try:
            futures = [pool.submit(fn, item) for item in work]
            done, pending = wait(futures, return_when=FIRST_EXCEPTION)
            for future in done:
                error = future.exception()
                if error is not None:
                    for other in pending:
                        other.cancel()
                    raise error
            return [future.result() for future in futures]
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

    def _fetch_pages(
        self,
        path: str,
        parse: Callable[[Any], tuple[list[dict[str, Any]], int, int, int]],
        params: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], int]:
        """Every page of a paged endpoint, plus the `total` it claims. Page 1 says how many pages there are."""
        rows, total, _page, page_size = parse(self._get_listing(path, params={**params, "page": 1}))
        if page_size <= 0:
            raise PortalParseError(f"{path} reports a page size of {page_size}")
        pages = math.ceil(total / page_size)
        if pages > _MAX_PAGES:
            raise PortalParseError(f"{path} claims {pages} pages")
        if pages > 1:

            def fetch(page: int) -> list[dict[str, Any]]:
                return parse(self._get_listing(path, params={**params, "page": page}))[0]

            for page_rows in self._map_concurrent(fetch, range(2, pages + 1)):
                rows.extend(page_rows)
        return rows, total

    def _zone(self) -> ZoneInfo:
        if self._tz is None:
            self._tz = ZoneInfo(self._timezone_name)
        return self._tz

    # -- Public data methods --------------------------------------------------------

    def get_meters(self) -> list[dict[str, Any]]:
        """Every meter: the paged `/portal/meters/search` with an empty query, the portal's bulk path.

        The row count is reconciled with the `total` the portal states, and a meter that shows up on
        two pages (the list changing while it is read) is kept once.
        """
        rows, total = self._fetch_pages("/portal/meters/search", parse_meter_page, {"q": ""})
        meters: dict[str, dict[str, Any]] = {}
        for row in rows:
            if row["meter_id"] in meters:
                logger.warning("meter list: %s appears twice (the list changed while it was read?)", row["meter_id"])
                continue
            meters[row["meter_id"]] = row
        if total > 0 and not meters:
            # An empty list that the portal itself says is not empty would be cached as the truth: refuse it.
            raise PortalParseError(f"the portal states {total} meters but none could be read")
        if len(meters) != total:
            logger.warning("meter list: the portal states %d meters but %d were read", total, len(meters))
        return list(meters.values())

    def get_meter_details(self, meter_id: str) -> dict[str, Any]:
        """Nameplate, place in the network and coordinates: the page's data endpoint plus `/geo`.

        Raises PortalNotFoundError for an id the portal does not know.
        """
        encoded = quote(meter_id, safe="")
        detail = parse_meter_detail(self._get_json(f"/meters/{encoded}/__data.json", params=_PAGE_NODE))
        try:
            detail["latitude"], detail["longitude"] = parse_geo(self._get_json(f"/portal/meters/{encoded}/geo"))
        except PortalNotFoundError:
            logger.warning("meter %s has a detail page but no geo record", meter_id)
        except PortalParseError as exc:  # the nameplate is what matters; a bad geo answer must not take it down too
            logger.warning("meter %s: coordinates unreadable: %s", meter_id, exc)
        return detail

    def get_meter_consumption(self, meter_id: str) -> list[dict[str, Any]]:
        """Half-hourly readings from `/portal/meters/<id>/energy`, oldest first.

        The portal takes no date range: it returns its whole window (seven days in the capture).
        """
        payload = self._get_json(f"/portal/meters/{quote(meter_id, safe='')}/energy")
        return parse_energy(payload, self._zone())

    def get_transformers(self) -> list[dict[str, Any]]:
        """The Transformers screen: every distribution transformer with its feeder and capacity."""
        rows, total = self._fetch_pages("/portal/dts", parse_dt_page, {})
        if len(rows) != total:
            logger.warning("transformer list: the portal states %d but %d were read", total, len(rows))
        return rows

    def get_coordinates(self, meter_ids: Iterable[str]) -> dict[str, tuple[float | None, float | None]]:
        """Latitude/longitude for many meters. There is no bulk geo endpoint: one `/geo` call per meter.

        A meter the portal has no usable coordinates for maps to (None, None). A meter whose `/geo` answer could not
        be read at all is left out of the result, so the caller does not remember "no coordinates" for what may be a
        passing glitch (a proxy's error page, say) and asks again next time.
        """

        unplaced: list[str] = []
        unreadable: list[str] = []

        def one(meter_id: str) -> tuple[str, tuple[float | None, float | None] | None]:
            try:
                return meter_id, parse_geo(self._get_json(f"/portal/meters/{quote(meter_id, safe='')}/geo"))
            except PortalNotFoundError:
                unplaced.append(meter_id)
                return meter_id, (None, None)
            except PortalParseError:
                unreadable.append(meter_id)
                return meter_id, None

        points = {
            meter_id: point for meter_id, point in self._map_concurrent(one, list(meter_ids)) if point is not None
        }
        # One line each, not one per meter: a portal with many bad records must not flood the log.
        if unplaced:
            logger.warning("no coordinates for %d meters, for example %s", len(unplaced), sorted(unplaced)[:3])
        if unreadable:
            logger.warning(
                "unreadable coordinates for %d meters, for example %s", len(unreadable), sorted(unreadable)[:3]
            )
        return points

    # -- Network hierarchy ----------------------------------------------------------

    def get_hierarchy(self) -> list[dict[str, Any]]:
        """The network tree, Zone down to DT, rebuilt from meter breadcrumbs (see `app/hierarchy.py`)."""
        tree: list[dict[str, Any]] = self._hierarchy_report()["tree"]
        return tree

    def get_hierarchy_issues(self) -> dict[str, Any]:
        """What does not add up in the hierarchy data, and what was checked to find out."""
        report = self._hierarchy_report()
        return {"checked": report["checked"], "issues": report["issues"]}

    def get_hierarchy_paths(self) -> dict[str, list[dict[str, Any]]]:
        """The breadcrumb of every transformer, by DT code: what the meters under it inherit."""
        paths: dict[str, list[dict[str, Any]]] = self._hierarchy_report()["paths"]
        return paths

    def _hierarchy_report(self) -> dict[str, Any]:
        """Tree, issues and paths in one crawl. Concurrent callers share it; it is reused for a short while."""
        with self._report_lock:
            memo = self._report_memo
            if memo is not None and time.monotonic() - memo[0] < _REPORT_MEMO_SECONDS:
                return memo[1]
            report = self._build_hierarchy_report()
            self._report_memo = (time.monotonic(), report)
            return report

    def _build_hierarchy_report(self) -> dict[str, Any]:
        """One breadcrumb per transformer, not per meter: 40 requests instead of 400.

        Meters hang off a transformer and the breadcrumb is the transformer's own path, so one
        representative meter per transformer is enough to learn it. What that assumes (every meter
        under a transformer shares the path) is the one thing this cannot check.
        """
        meters = self.get_meters()
        transformers = self.get_transformers()
        counts: dict[str, int] = {}
        representative: dict[str, str] = {}
        for meter in meters:
            code = meter.get("dt_code")
            if code:
                counts[code] = counts.get(code, 0) + 1
                representative.setdefault(code, meter["meter_id"])

        def breadcrumb(item: tuple[str, str]) -> tuple[str, list[dict[str, Any]] | None]:
            dt_code, meter_id = item
            try:
                page = self._get_json(f"/meters/{quote(meter_id, safe='')}/__data.json", params=_PAGE_NODE)
                return dt_code, parse_meter_detail(page)["hierarchy"]
            except (PortalNotFoundError, PortalParseError) as exc:
                logger.warning("hierarchy: no breadcrumb for %s under %s: %s", meter_id, dt_code, exc)
                return dt_code, None

        paths: dict[str, list[dict[str, Any]]] = {}
        for dt_code, levels in self._map_concurrent(breadcrumb, sorted(representative.items())):
            if levels is not None:
                paths[dt_code] = levels
        tree, issues = build_hierarchy(paths, meter_counts=counts, transformers=transformers)
        for dt_code in sorted(set(representative) - set(paths)):
            sample = representative[dt_code]
            issues.append(
                {
                    "type": "unreadable_breadcrumb",
                    "id": dt_code,
                    "detail": f"the detail page of {sample}, the sample for {dt_code}, could not be read",
                }
            )
        no_dt = [meter["meter_id"] for meter in meters if not meter.get("dt_code")]
        if no_dt:
            detail = f"{len(no_dt)} meters carry no transformer code: {no_dt[:5]}"
            issues.append({"type": "meter_without_dt", "id": no_dt[0], "detail": detail})
        return {
            "tree": tree,
            "issues": issues,
            "paths": paths,
            "checked": {"meters": len(meters), "transformers": len(transformers), "breadcrumbs": len(paths)},
        }
