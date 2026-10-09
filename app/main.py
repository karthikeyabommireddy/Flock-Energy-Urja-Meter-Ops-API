"""Application entry point and API route definitions.

Session management is fully automatic: no endpoint here requires the caller to log in
first. `UrjaPortalClient.get_*` methods call `_ensure_authenticated()` themselves on
first use and re-authenticate transparently if the session expires mid-flight. The
explicit `POST /api/v1/auth/login` endpoint exists for callers who want to force a
fresh login (e.g. to verify credentials without fetching data), not because it's
required for normal use.

v1.1 extends v1 without removing anything: every v1 route keeps its path and field names. New
behaviour arrives as new query parameters, response headers and new routes. What v1.1 changes
on purpose: a value the portal does not give is `null` instead of "N/A" / "UNKNOWN"; a portal
that is down answers 503 (504 for a timeout, both with a hint to retry) instead of 502, while
any other upstream failure stays 502; errors are problem+json that keeps the v1 `detail`.
New:

- `X-Cache: HIT | MISS | STALE` on the data routes (see `app/cache.py`). STALE means the
  portal failed and the last good data was served instead of an error.
- `GET /api/v1/meters` can be filtered (status, make, phase, transformer, network branch,
  a radius around a point), searched, paged (`limit` / `offset`, with `X-Total-Count`)
  and enriched (`include=coordinates,hierarchy`); see `app/service.py` for what each costs.
- `GET /api/v1/transformers`, `/api/v1/fleet/summary` and `/api/v1/hierarchy/issues`.
- `GET /health/ready`: can we hold a session with the portal?
- Errors are RFC 9457 `application/problem+json` that still carry the v1 `detail`
  message, plus a stable `code` and the request id (also sent as `X-Request-ID`).
"""

from __future__ import annotations

import logging
import re
import threading
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from http import HTTPStatus
from typing import Annotated, Any, Literal
from weakref import WeakKeyDictionary

from fastapi import Depends, FastAPI, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BeforeValidator
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.client import (
    PortalOutageError,
    PortalTimeoutError,
    PortalUnavailableError,
    SessionExpiredError,
    UrjaPortalClient,
)
from app.config import settings
from app.errors import PortalNotFoundError, PortalParseError
from app.models import (
    ConsumptionHistory,
    ConsumptionRecord,
    ErrorDetail,
    FleetSummary,
    HierarchyNode,
    HierarchyReport,
    LoginResult,
    MeterDetail,
    MeterSummary,
    Transformer,
)
from app.service import MeterQuery, MeterService, ServiceState, interval_minutes

_REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_LOG_LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}
log = logging.getLogger("urja_api")


class InvalidRequest(Exception):
    """A request that is well-formed but asks for something that makes no sense (answered with 422)."""


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    logging.basicConfig(level=_LOG_LEVELS[settings.log_level], format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # At DEBUG, httpcore logs every response header of every exchange, and the login response's Set-Cookie is the
    # session. Our own loggers follow `log_level`; the HTTP libraries never go below WARNING.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, _LOG_LEVELS[settings.log_level]))
    try:  # warn rather than refuse to start: only consumption needs the zone database
        from zoneinfo import ZoneInfo

        ZoneInfo(settings.portal_timezone)
    except Exception:  # a missing tzdata package and a typo look the same here
        log.warning(
            "time zone %r could not be loaded: consumption requests will fail until it is valid "
            "(pip install tzdata, or fix URJA_PORTAL_TIMEZONE)",
            settings.portal_timezone,
        )
    yield
    with _client_lock:
        if _client is not None:  # only if a request ever created it
            _client.close()


app = FastAPI(
    title=settings.api_title,
    version=settings.api_version,
    description=settings.api_description,
    openapi_tags=[
        {"name": "meters", "description": "Smart meters: list and filter, detail, consumption history."},
        {"name": "hierarchy", "description": "The organised network tree and its data-quality report."},
        {"name": "fleet", "description": "Transformers and fleet-level counts."},
        {"name": "auth", "description": "Sessions are automatic; this only forces a fresh login."},
        {"name": "meta", "description": "Liveness and readiness probes."},
    ],
    lifespan=lifespan,
)

_generate_openapi = app.openapi


def _openapi_with_problem_json() -> dict[str, Any]:
    """FastAPI documents error models as application/json; we really send application/problem+json."""
    if app.openapi_schema is None:
        schema = _generate_openapi()
        for operation in (op for path in schema["paths"].values() for op in path.values()):
            for status, response in operation.get("responses", {}).items():
                content = response.get("content", {})
                if status[0] in "45" and "application/json" in content:
                    response["content"] = {"application/problem+json": content["application/json"]}
        app.openapi_schema = schema
    return app.openapi_schema


app.openapi = _openapi_with_problem_json  # type: ignore[method-assign]


_client: UrjaPortalClient | None = None
_client_lock = threading.Lock()


def _get_client_singleton() -> UrjaPortalClient:
    """One adapter instance per process — holds the one authenticated session.

    Built under a lock: the first requests of a cold process arrive together, and each of them building its own
    client would mean its own session, its own login and its own concurrency cap (so, in effect, none).
    """
    global _client
    with _client_lock:
        if _client is None:
            _client = UrjaPortalClient(
                base_url=settings.portal_base_url,
                email=settings.portal_email,
                password=settings.portal_password.get_secret_value(),
                timeout=settings.request_timeout_seconds,
                max_retries=settings.max_retries,
                max_concurrency=settings.max_concurrency,
                timezone=settings.portal_timezone,
            )
        return _client


def get_client() -> UrjaPortalClient:
    return _get_client_singleton()


PortalClient = Annotated[UrjaPortalClient, Depends(get_client)]

# The cache and the readiness memo live with the client they describe: one session, one
# cache. Keyed weakly, so a replaced client (a test double, say) takes its cache with it.
_states: WeakKeyDictionary[Any, ServiceState] = WeakKeyDictionary()
_states_lock = threading.Lock()


def get_service(client: PortalClient) -> MeterService:
    with _states_lock:
        state = _states.get(client)
        if state is None:
            state = _states[client] = ServiceState(settings)
    return MeterService(client, settings, state)


Service = Annotated[MeterService, Depends(get_service)]

# Meter ids end up inside portal URLs, so only plain id characters are accepted. The portal's ids
# are a letter and six digits (J100000); anything with a separator, escape or dot cannot be an id.
MeterId = Annotated[
    str,
    Path(
        pattern=r"^[A-Za-z0-9_-]{1,32}$",
        description="Meter id, as the portal prints it.",
        examples=["J100000"],
    ),
]


def _split_commas(value: Any) -> Any:
    """`include=coordinates,hierarchy` and `include=coordinates&include=hierarchy` mean the same."""
    if isinstance(value, str):
        value = [value]
    if isinstance(value, list):
        return [part.strip() for item in value for part in str(item).split(",") if part.strip()]
    return value


Include = Literal["coordinates", "hierarchy"]

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorDetail, "description": "The portal rejected the service account's credentials"},
    502: {"model": ErrorDetail, "description": "The portal answered with something we could not use"},
    503: {"model": ErrorDetail, "description": "Portal unreachable; retry after the Retry-After delay"},
    504: {"model": ErrorDetail, "description": "The portal timed out"},
    500: {"model": ErrorDetail, "description": "An unexpected error in this API (details are in its log, not here)"},
}
# Routes with query or path parameters can also answer 422 (declared explicitly so the contract shows our
# problem body rather than FastAPI's default validation schema), and routes about one thing can answer 404.
_VALIDATION_ERROR: dict[int | str, dict[str, Any]] = {
    422: {"model": ErrorDetail, "description": "Invalid query or path parameter"}
}
_NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"model": ErrorDetail, "description": "Unknown meter or node"}}
_X_CACHE: dict[str, Any] = {
    "X-Cache": {
        "description": (
            "HIT: fresh cache. MISS: loaded from the portal. STALE: the portal failed, last good data was served."
        ),
        "schema": {"type": "string", "enum": ["HIT", "MISS", "STALE"]},
    }
}
_X_TOTAL_COUNT: dict[str, Any] = {
    "X-Total-Count": {
        "description": "Meters matching the filters, before limit and offset are applied",
        "schema": {"type": "integer"},
    }
}


# -- Request ids and errors ---------------------------------------------------------


@app.middleware("http")
async def request_id(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    incoming = request.headers.get("x-request-id", "")
    request.state.request_id = incoming if _REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex
    try:
        response = await call_next(request)
    except Exception:
        # Answered here, inside the CORS middleware: the handler for `Exception` below runs outside it, and a 500
        # sent from there carries no CORS headers, so a browser client could not even read what went wrong.
        log.exception("unhandled error (request %s)", request.state.request_id)
        response = problem(request, 500, "internal_error", "Internal Server Error", "An unexpected error occurred.")
    response.headers["X-Request-ID"] = request.state.request_id
    return response


if settings.cors_origins:  # added last, so it is outermost and answers pre-flight requests itself
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET"],
        allow_headers=["X-Request-ID"],
        expose_headers=["X-Cache", "X-Total-Count", "X-Request-ID", "Retry-After"],
    )


def problem(
    request: Request,
    status: int,
    code: str,
    title: str,
    detail: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    body = ErrorDetail(title=title, status=status, code=code, detail=detail, request_id=request_id)
    out_headers = dict(headers or {})
    if request_id:
        out_headers["X-Request-ID"] = request_id  # errors raised outside the middleware stack skip the middleware
    return JSONResponse(
        body.model_dump(exclude_none=True),
        status_code=status,
        media_type="application/problem+json",
        headers=out_headers,
    )


@app.exception_handler(SessionExpiredError)
async def on_session_expired(request: Request, exc: SessionExpiredError) -> JSONResponse:
    detail = f"Could not authenticate with the portal: {exc}"
    return problem(request, 401, "upstream_auth_failed", "Portal authentication failed", detail)


@app.exception_handler(PortalUnavailableError)
async def on_portal_unavailable(request: Request, exc: PortalUnavailableError) -> JSONResponse:
    if isinstance(exc, PortalTimeoutError):
        status, code = 504, "upstream_timeout"
    elif isinstance(exc, PortalOutageError):
        status, code = 503, "upstream_unavailable"
    else:
        status, code = 502, "upstream_error"
    headers = {"Retry-After": "30"} if status == 503 else None
    return problem(request, status, code, "Upstream portal error", f"Legacy portal unavailable: {exc}", headers)


@app.exception_handler(PortalParseError)
async def on_portal_unparseable(request: Request, exc: PortalParseError) -> JSONResponse:
    detail = f"The portal answered in a shape this API does not recognise: {exc}"
    return problem(request, 502, "upstream_unparseable", "Upstream portal error", detail)


@app.exception_handler(PortalNotFoundError)
async def on_not_found(request: Request, exc: PortalNotFoundError) -> JSONResponse:
    return problem(request, 404, "not_found", "Not Found", str(exc))


@app.exception_handler(InvalidRequest)
async def on_invalid(request: Request, exc: InvalidRequest) -> JSONResponse:
    return problem(request, 422, "invalid_request", "Invalid request", str(exc))


@app.exception_handler(StarletteHTTPException)
async def on_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    if exc.status_code == 404:
        return problem(request, 404, "not_found", "Not Found", f"No route for {request.method} {request.url.path}")
    try:
        title = HTTPStatus(exc.status_code).phrase
    except ValueError:
        title = "HTTP Error"
    # Headers the framework attached (a 405 must say which methods are allowed, `Allow: GET`) stay on the answer.
    return problem(request, exc.status_code, "http_error", title, str(exc.detail), dict(exc.headers or {}))


@app.exception_handler(RequestValidationError)
async def on_invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
    detail = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
    return problem(request, 422, "invalid_request", "Invalid request", detail)


@app.exception_handler(Exception)
async def on_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    # Never leak internals to the caller. The server still logs the traceback: Starlette re-raises
    # after sending this response.
    return problem(request, 500, "internal_error", "Internal Server Error", "An unexpected error occurred.")


# -- Routes ------------------------------------------------------------------------


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    # `async` on purpose: it does no I/O and so runs on the event loop, not in the worker pool the other routes
    # share. A liveness probe must still be answered when every worker is busy waiting on the portal.
    return {"status": "ok"}


@app.get("/health/ready", tags=["meta"], responses=_ERROR_RESPONSES)
def ready(service: Service) -> dict[str, str]:
    """Readiness: can we hold a session with the portal?

    Unlike /health this talks to the portal, though at most once every few seconds
    however often it is probed.
    """
    service.check_portal()
    return {"status": "ready"}


_LOGIN_REFUSALS = {
    "csrf_origin": "The portal refused the login form post (HTTP 403, its cross-site check), not the credentials.",
    "cooldown": "Not retrying yet: the portal refused the last login attempt moments ago.",
}


@app.post("/api/v1/auth/login", response_model=LoginResult, tags=["auth"], responses=_ERROR_RESPONSES)
def login(client: PortalClient) -> LoginResult:
    """Force a fresh login against the portal. Not required before other calls."""
    ok = client.login()
    if ok:
        return LoginResult(authenticated=True, detail="Authenticated successfully.")
    reason: str = getattr(client, "last_login_error", None) or ""  # test doubles have no such attribute
    return LoginResult(authenticated=False, detail=_LOGIN_REFUSALS.get(reason, "Portal rejected the credentials."))


@app.get(
    "/api/v1/meters",
    response_model=list[MeterSummary],
    response_model_exclude_unset=True,  # latitude, longitude, distance_m and hierarchy_path appear only when asked for
    tags=["meters"],
    summary="List, filter and search meters",
    responses={200: {"headers": {**_X_CACHE, **_X_TOTAL_COUNT}}, **_VALIDATION_ERROR, **_ERROR_RESPONSES},
)
def list_meters(
    response: Response,
    service: Service,
    status: Annotated[
        str | None, Query(max_length=32, description="Only meters in this status (case-insensitive), e.g. FAULTY")
    ] = None,
    q: Annotated[
        str | None, Query(min_length=1, max_length=64, description="Substring of the meter id or serial number")
    ] = None,
    make: Annotated[
        str | None, Query(max_length=32, description="Manufacturer, case-insensitive, e.g. HPL or L&T")
    ] = None,
    phase: Annotated[str | None, Query(max_length=16, description="single or three")] = None,
    dt: Annotated[str | None, Query(max_length=32, description="Transformer code, e.g. DT-007")] = None,
    zone: Annotated[str | None, Query(max_length=32, description="Network branch: zone code, e.g. Z-01")] = None,
    circle: Annotated[str | None, Query(max_length=32, description="Network branch: circle code, e.g. C-01")] = None,
    division: Annotated[str | None, Query(max_length=32, description="Network branch: division code")] = None,
    subdivision: Annotated[str | None, Query(max_length=32, description="Network branch: subdivision code")] = None,
    substation: Annotated[str | None, Query(max_length=32, description="Network branch: substation code")] = None,
    feeder: Annotated[str | None, Query(max_length=32, description="Network branch: feeder code, e.g. F-001")] = None,
    lat: Annotated[float | None, Query(ge=-90, le=90, description="Latitude of the search point")] = None,
    lon: Annotated[float | None, Query(ge=-180, le=180, description="Longitude of the search point")] = None,
    radius_m: Annotated[
        float | None,
        Query(gt=0, le=100_000, description="Only meters within this many metres of lat/lon, nearest first"),
    ] = None,
    include: Annotated[
        list[Include] | None,
        Query(
            description=(
                "Extra fields, comma separated or repeated. coordinates: latitude/longitude (one portal request "
                "per meter, fetched once). hierarchy: the codes from zone down to the transformer."
            )
        ),
        BeforeValidator(_split_commas),  # next to Query, not inside the list type: FastAPI must still see a list
    ] = None,
    limit: Annotated[int | None, Query(ge=1, le=500, description="Page size; omit to get every meter")] = None,
    offset: Annotated[int, Query(ge=0, description="Meters to skip")] = 0,
) -> list[MeterSummary]:
    near: tuple[float, float, float] | None = None
    if lat is not None or lon is not None or radius_m is not None:
        if lat is None or lon is None or radius_m is None:
            raise InvalidRequest("lat, lon and radius_m must be given together")
        near = (lat, lon, radius_m)
    branch = {
        level: code
        for level, code in (
            ("zone", zone),
            ("circle", circle),
            ("division", division),
            ("subdivision", subdivision),
            ("substation", substation),
            ("feeder", feeder),
        )
        if code
    }
    query = MeterQuery(
        status=status,
        q=q,
        make=make,
        phase=phase,
        dt=dt,
        hierarchy=branch,
        near=near,
        include=frozenset(include or ()),
        limit=limit,
        offset=offset,
    )
    items, total, cache = service.list_meters(query)
    response.headers["X-Cache"] = cache
    response.headers["X-Total-Count"] = str(total)
    return [MeterSummary(**item) for item in items]


@app.get(
    "/api/v1/meters/{meter_id}",
    response_model=MeterDetail,
    tags=["meters"],
    responses={200: {"headers": _X_CACHE}, **_NOT_FOUND, **_VALIDATION_ERROR, **_ERROR_RESPONSES},
)
def get_meter(meter_id: MeterId, response: Response, service: Service) -> MeterDetail:
    raw, cache = service.get_meter(meter_id)
    response.headers["X-Cache"] = cache
    return MeterDetail(**raw)


def _utc(moment: datetime | None) -> datetime | None:
    """A query-string time with no zone is read as UTC."""
    return moment if moment is None or moment.tzinfo else moment.replace(tzinfo=timezone.utc)


@app.get(
    "/api/v1/meters/{meter_id}/consumption",
    response_model=ConsumptionHistory,
    tags=["meters"],
    summary="Consumption history",
    responses={200: {"headers": _X_CACHE}, **_NOT_FOUND, **_VALIDATION_ERROR, **_ERROR_RESPONSES},
)
def get_meter_consumption(
    meter_id: MeterId,
    response: Response,
    service: Service,
    start: Annotated[
        datetime | None,
        Query(alias="from", description="Only readings at or after this time (RFC 3339 or a date; no zone means UTC)"),
    ] = None,
    end: Annotated[
        datetime | None,
        Query(alias="to", description="Only readings at or before this time (RFC 3339 or a date; no zone means UTC)"),
    ] = None,
) -> ConsumptionHistory:
    """The meter's half-hourly readings.

    The portal serves one fixed window and takes no date range, so `from` / `to` cut that window here.
    `value` is the cumulative kWh register; `interval_kwh` is the consumption in each interval.
    """
    start_utc, end_utc = _utc(start), _utc(end)
    if start_utc is not None and end_utc is not None and start_utc > end_utc:
        raise InvalidRequest("'from' must not be later than 'to'")
    canonical, raw, cache = service.get_consumption(meter_id, start_utc, end_utc)
    response.headers["X-Cache"] = cache
    return ConsumptionHistory(
        meter_id=canonical,
        records=[ConsumptionRecord(**r) for r in raw],
        interval_minutes=interval_minutes(raw),
        portal_timezone=settings.portal_timezone,
    )


@app.get(
    "/api/v1/hierarchy",
    response_model=list[HierarchyNode],
    tags=["hierarchy"],
    responses={200: {"headers": _X_CACHE}, **_NOT_FOUND, **_VALIDATION_ERROR, **_ERROR_RESPONSES},
)
def get_hierarchy(
    response: Response,
    service: Service,
    root: Annotated[str | None, Query(max_length=32, description="Only the subtree under this node code")] = None,
    depth: Annotated[
        int | None, Query(ge=0, le=10, description="Levels below the root to include (0 = the node alone)")
    ] = None,
) -> list[HierarchyNode]:
    """Optional extension — the organised network tree, Zone down to transformer."""
    raw, cache = service.get_hierarchy(root, depth)
    response.headers["X-Cache"] = cache
    return [HierarchyNode(**node) for node in raw]


@app.get(
    "/api/v1/hierarchy/issues",
    response_model=HierarchyReport,
    tags=["hierarchy"],
    summary="Hierarchy data-quality report",
    responses={200: {"headers": _X_CACHE}, **_ERROR_RESPONSES},
)
def get_hierarchy_issues(response: Response, service: Service) -> HierarchyReport:
    """What does not add up in the network data: nodes under two parents, mismatched names or feeders,
    transformers without meters, meters pointing at unknown transformers."""
    raw, cache = service.get_hierarchy_issues()
    response.headers["X-Cache"] = cache
    return HierarchyReport(**raw)


@app.get(
    "/api/v1/transformers",
    response_model=list[Transformer],
    tags=["fleet"],
    responses={200: {"headers": _X_CACHE}, **_ERROR_RESPONSES},
)
def list_transformers(response: Response, service: Service) -> list[Transformer]:
    """Distribution transformers with their feeder, capacity and how many meters hang off them."""
    raw, cache = service.get_transformers()
    response.headers["X-Cache"] = cache
    return [Transformer(**t) for t in raw]


@app.get(
    "/api/v1/fleet/summary",
    response_model=FleetSummary,
    tags=["fleet"],
    responses={200: {"headers": _X_CACHE}, **_ERROR_RESPONSES},
)
def get_fleet_summary(response: Response, service: Service) -> FleetSummary:
    """Counts by status, make, phase and transformer, for a dashboard."""
    raw, cache = service.get_summary()
    response.headers["X-Cache"] = cache
    return FleetSummary(**raw)
