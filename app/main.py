"""Application entry point and API route definitions.

Session management is fully automatic: no endpoint here requires the caller to log in
first. `UrjaPortalClient.get_*` methods call `_ensure_authenticated()` themselves on
first use and re-authenticate transparently if the session expires mid-flight. The
explicit `POST /api/v1/auth/login` endpoint exists for callers who want to force a
fresh login (e.g. to verify credentials without fetching data), not because it's
required for normal use.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import RedirectResponse

from app.client import (
    PortalUnavailableError,
    SessionExpiredError,
    UrjaPortalClient,
)
from app.config import settings
from app.models import (
    ConsumptionHistory,
    ConsumptionRecord,
    HierarchyNode,
    LoginResult,
    MeterDetail,
    MeterSummary,
)

app = FastAPI(
    title=settings.api_title,
    version=settings.api_version,
    description=settings.api_description,
)


@lru_cache
def _get_client_singleton() -> UrjaPortalClient:
    """One adapter instance per process — holds the one authenticated session."""
    return UrjaPortalClient(
        base_url=settings.portal_base_url,
        email=settings.portal_email,
        password=settings.portal_password,
        timeout=settings.request_timeout_seconds,
    )


def get_client() -> UrjaPortalClient:
    return _get_client_singleton()


PortalClient = Annotated[UrjaPortalClient, Depends(get_client)]


def _map_portal_errors(exc: Exception) -> HTTPException:
    """Every portal-adapter exception maps to one HTTP status — never a bare 500 leak."""
    if isinstance(exc, SessionExpiredError):
        return HTTPException(status_code=401, detail=f"Could not authenticate with the portal: {exc}")
    if isinstance(exc, PortalUnavailableError):
        return HTTPException(status_code=502, detail=f"Legacy portal unavailable: {exc}")
    return HTTPException(status_code=500, detail=f"Unexpected error: {exc}")


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/auth/login", response_model=LoginResult, tags=["auth"])
def login(client: PortalClient) -> LoginResult:
    """Force a fresh login against the portal. Not required before other calls."""
    try:
        ok = client.login()
    except PortalUnavailableError as exc:
        raise _map_portal_errors(exc) from exc
    return LoginResult(
        authenticated=ok,
        detail="Authenticated successfully." if ok else "Portal rejected the credentials.",
    )


@app.get("/api/v1/meters", response_model=list[MeterSummary], tags=["meters"])
def list_meters(client: PortalClient) -> list[MeterSummary]:
    try:
        raw = client.get_meters()
    except (SessionExpiredError, PortalUnavailableError) as exc:
        raise _map_portal_errors(exc) from exc
    return [MeterSummary(**item) for item in raw]


@app.get("/api/v1/meters/{meter_id}", response_model=MeterDetail, tags=["meters"])
def get_meter(meter_id: str, client: PortalClient) -> MeterDetail:
    try:
        raw = client.get_meter_details(meter_id)
    except (SessionExpiredError, PortalUnavailableError) as exc:
        raise _map_portal_errors(exc) from exc
    return MeterDetail(**raw)


@app.get(
    "/api/v1/meters/{meter_id}/consumption",
    response_model=ConsumptionHistory,
    tags=["meters"],
)
def get_meter_consumption(meter_id: str, client: PortalClient) -> ConsumptionHistory:
    try:
        raw = client.get_meter_consumption(meter_id)
    except (SessionExpiredError, PortalUnavailableError) as exc:
        raise _map_portal_errors(exc) from exc
    return ConsumptionHistory(meter_id=meter_id, records=[ConsumptionRecord(**r) for r in raw])


@app.get("/api/v1/hierarchy", response_model=list[HierarchyNode], tags=["hierarchy"])
def get_hierarchy(client: PortalClient) -> list[HierarchyNode]:
    """Optional extension — the organised network tree."""
    try:
        raw = client.get_hierarchy()
    except (SessionExpiredError, PortalUnavailableError) as exc:
        raise _map_portal_errors(exc) from exc
    return [HierarchyNode(**node) for node in raw]
