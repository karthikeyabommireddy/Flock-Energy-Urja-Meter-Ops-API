"""Response schemas for the Flock Energy API.

These are the shapes downstream clients see — deliberately independent of whatever
shape the legacy portal happens to return. `app/client.py` is responsible for mapping
raw portal data (its JSON and SvelteKit page data) into these models; a portal quirk
should never leak through as an API quirk.

v1.1 only adds: v1 fields keep their names and meaning, new fields are optional, and a few
v1 fields were widened to `str | None` so that a value the portal does not give is `null`
rather than a placeholder string such as "N/A".
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class HierarchyLevel(BaseModel):
    """One step of a meter's breadcrumb through the network."""

    level: str = Field(description="zone, circle, division, subdivision, substation, feeder or dt")
    name: str | None = Field(default=None, description="e.g. 'Jaipur Zone 2'")
    code: str | None = Field(default=None, description="e.g. 'Z-02'")


class MeterSummary(BaseModel):
    """One row of the meter listing — GET /api/v1/meters."""

    meter_id: str
    serial_number: str | None = None
    status: str | None = Field(
        default=None,
        description="Installation status as the portal reports it, upper-cased: INSTALLED, FAULTY or DECOMMISSIONED",
    )
    location: str | None = Field(
        default=None, description="Null in the list; the meter's detail carries the transformer it hangs off"
    )
    make: str | None = Field(default=None, description="Manufacturer, e.g. HPL, L&T, Genus, Allied, Secure")
    phase_type: str | None = Field(default=None, description="single or three")
    dt_code: str | None = Field(default=None, description="Distribution transformer the meter hangs off, e.g. DT-007")
    latitude: float | None = Field(
        default=None, description="Only filled when asked for (include=coordinates) or when a geo filter is used"
    )
    longitude: float | None = None
    distance_m: float | None = Field(default=None, description="Metres from lat/lon; only with the geo filter")
    hierarchy_path: list[str] | None = Field(
        default=None, description="Codes from zone down to DT, e.g. ['Z-01', 'C-01', ...]; only with include=hierarchy"
    )


class MeterDetail(BaseModel):
    """A single meter's full detail — GET /api/v1/meters/{id}."""

    meter_id: str
    serial_number: str | None = None
    status: str | None = None
    location: str | None = Field(
        default=None,
        description=(
            "The transformer the meter hangs off, which is the portal's closest thing to a place name. "
            "Coordinates are in latitude / longitude."
        ),
    )
    installed_date: str | None = Field(
        default=None, description="The portal shows no installation date, so this is always null"
    )
    make: str | None = None
    phase_type: str | None = None
    installation_type: str | None = Field(default=None, description="e.g. 'Whole Current' or 'CT Operated'")
    dt_code: str | None = None
    latitude: float | None = Field(default=None, description="Null when the portal has no usable coordinates")
    longitude: float | None = None
    hierarchy: list[HierarchyLevel] = Field(
        default_factory=list, description="Breadcrumb from zone down to the transformer, as the portal shows it"
    )
    raw_fields: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Any other parameter the portal lists for the meter that does not map to a named "
            "field above, kept rather than dropped. Only five meters' details were captured "
            "(see PROTOCOL.md), so other meters may carry more."
        ),
    )


class ConsumptionRecord(BaseModel):
    """One data point in a consumption history."""

    period: str = Field(
        description="Reading time, RFC 3339 UTC (2026-06-23T18:00:00Z). Readings whose time cannot be read are left out"
    )
    value: float | None = Field(
        description=(
            "The meter's cumulative kWh register at that time, not the consumption in the interval "
            "(see interval_kwh). null if unparseable"
        )
    )
    unit: str | None = None
    kvah: float | None = Field(default=None, description="Cumulative kVAh register")
    voltage_r: float | None = Field(default=None, description="R-phase voltage in volts")
    interval_kwh: float | None = Field(
        default=None,
        description=(
            "kWh consumed since the reading one step earlier. Null for the first reading, after a missing or "
            "unreadable reading (the difference would span several intervals) and where the register fell"
        ),
    )


class ConsumptionHistory(BaseModel):
    """GET /api/v1/meters/{id}/consumption."""

    meter_id: str
    records: list[ConsumptionRecord]
    interval_minutes: int | None = Field(
        default=None, description="Spacing of the readings (30 for this portal), when they are evenly spaced"
    )
    portal_timezone: str | None = Field(
        default=None,
        description=(
            "The zone the portal's local timestamps were read in before being converted to UTC. "
            "The portal states none, so this is an assumption."
        ),
    )


class HierarchyNode(BaseModel):
    """A node in the network hierarchy tree — GET /api/v1/hierarchy (optional extension)."""

    node_id: str = Field(description="The node's code, e.g. Z-01 or DT-007")
    name: str | None = None
    node_type: str | None = Field(
        default=None, description="zone, circle, division, subdivision, substation, feeder or dt"
    )
    children: list[HierarchyNode] = Field(default_factory=list)
    meter_count: int | None = Field(default=None, description="Meters at or below this node")
    capacity_kva: float | None = Field(default=None, description="Transformer capacity (dt nodes only)")


class HierarchyIssue(BaseModel):
    """One thing in the hierarchy data that does not add up."""

    type: str = Field(
        description=(
            "multiple_parents, name_mismatch, incomplete_path, dt_mismatch, feeder_mismatch, dt_without_meters, "
            "meter_unknown_dt, meter_without_dt, unreadable_breadcrumb, missing_code or unknown_level"
        )
    )
    id: str = Field(description="The node or transformer the issue is about")
    detail: str


class HierarchyReport(BaseModel):
    """GET /api/v1/hierarchy/issues: the hierarchy's data-quality report."""

    checked: dict[str, int] = Field(description="What was examined: meters, transformers and breadcrumbs read")
    issues: list[HierarchyIssue]


class Transformer(BaseModel):
    """A distribution transformer — GET /api/v1/transformers."""

    code: str
    name: str | None = None
    feeder_code: str | None = None
    capacity_kva: float | None = None
    meter_count: int | None = Field(default=None, description="Meters that point at this transformer")


class FleetSummary(BaseModel):
    """GET /api/v1/fleet/summary: counts for a dashboard, from the meter and transformer lists."""

    total_meters: int
    by_status: dict[str, int]
    by_make: dict[str, int]
    by_phase: dict[str, int]
    by_dt: dict[str, int] = Field(description="Meters per transformer code")
    transformers: int


class LoginResult(BaseModel):
    """POST /api/v1/auth/login."""

    authenticated: bool
    detail: str


class ErrorDetail(BaseModel):
    """Consistent error body shape for every non-2xx response this API returns.

    RFC 9457 problem details (`application/problem+json`) that keep the `detail` message
    v1 clients already read. `code` is stable: branch on it, not on `detail`.
    """

    type: str = "about:blank"
    title: str
    status: int
    code: str = Field(description="Stable machine-readable error code, e.g. upstream_unavailable")
    detail: str
    request_id: str | None = Field(default=None, description="Also sent as the X-Request-ID response header")
