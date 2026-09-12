"""Response schemas for the Flock Energy API.

These are the shapes downstream clients see — deliberately independent of whatever
shape the legacy portal happens to return. `app/client.py` is responsible for mapping
raw portal data (JSON or parsed HTML) into these models; a portal quirk should never
leak through as an API quirk.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class MeterSummary(BaseModel):
    """One row of the meter listing — GET /api/v1/meters."""

    meter_id: str
    serial_number: str
    status: str = Field(description="e.g. ACTIVE, INACTIVE, FAULT, UNKNOWN")
    location: str | None = None


class MeterDetail(BaseModel):
    """A single meter's full detail — GET /api/v1/meters/{id}."""

    meter_id: str
    serial_number: str
    status: str
    location: str | None = None
    installed_date: str | None = Field(
        default=None, description="ISO 8601 date if the portal provided a parseable one"
    )
    raw_fields: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Any additional fields the portal exposed that don't map to a named "
            "field above. Kept rather than discarded, since the portal's exact "
            "field set was not confirmed against a live session — see PROTOCOL.md."
        ),
    )


class ConsumptionRecord(BaseModel):
    """One data point in a consumption history."""

    period: str = Field(description="ISO 8601 date/timestamp, or the raw label if unparseable")
    value: float | None = Field(description="Consumption value, normalised to float. null if unparseable")
    unit: str | None = None


class ConsumptionHistory(BaseModel):
    """GET /api/v1/meters/{id}/consumption."""

    meter_id: str
    records: list[ConsumptionRecord]


class HierarchyNode(BaseModel):
    """A node in the network hierarchy tree — GET /api/v1/hierarchy (optional extension)."""

    node_id: str
    name: str
    node_type: str | None = None
    children: list[HierarchyNode] = Field(default_factory=list)


class LoginResult(BaseModel):
    """POST /api/v1/auth/login."""

    authenticated: bool
    detail: str


class ErrorDetail(BaseModel):
    """Consistent error body shape for every non-2xx response this API returns."""

    detail: str
