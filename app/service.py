"""Use-cases on top of the portal adapter: caching, filtering, pagination and the local index.

Knows the domain, not HTTP and not HTML. It works with anything that has the adapter's
`get_*` surface, so the routes can be exercised against a stand-in as easily as against
the real `UrjaPortalClient`.

The "local index" is the meter list held in memory, enriched on demand in tiers so that a
simple query never pays for an expensive one:

1. attributes (status, make, phase, transformer, search text): the list itself, about 21 portal requests;
2. network position (zone ... feeder): one breadcrumb per transformer, about 40 more requests;
3. coordinates (radius search, the map): one `/geo` request per meter, the dearest tier. They are
   fetched only for the meters a request actually needs, then kept for an hour.
"""

from __future__ import annotations

import copy
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import pairwise
from typing import TYPE_CHECKING, Any, Protocol

from app.cache import FAILURE_MEMO_SECONDS, CacheStatus, TTLCache
from app.client import PortalUnavailableError, SessionExpiredError
from app.errors import PortalNotFoundError, PortalParseError
from app.geo import bounding_box, haversine_m

if TYPE_CHECKING:  # only for annotations: importing the service must not need the portal credentials
    from app.config import Settings

READY_CACHE_SECONDS = 15.0  # readiness gets probed often; don't turn every probe into a portal request
_RANK: dict[CacheStatus, int] = {"HIT": 0, "MISS": 1, "STALE": 2}
# A portal that is down, rejecting our credentials or answering in a new layout is a reason to
# serve what we last knew, for the list and the coordinates alike.
_STALE_ON = (PortalUnavailableError, SessionExpiredError, PortalParseError)
Point = tuple[float | None, float | None]


class PortalSource(Protocol):
    """The slice of `UrjaPortalClient` the service depends on."""

    def get_meters(self) -> list[dict[str, Any]]: ...

    def get_meter_details(self, meter_id: str) -> dict[str, Any]: ...

    def get_meter_consumption(self, meter_id: str) -> list[dict[str, Any]]: ...

    def get_transformers(self) -> list[dict[str, Any]]: ...

    def get_hierarchy(self) -> list[dict[str, Any]]: ...

    def get_hierarchy_issues(self) -> dict[str, Any]: ...

    def get_hierarchy_paths(self) -> dict[str, list[dict[str, Any]]]: ...

    def get_coordinates(self, meter_ids: Iterable[str]) -> dict[str, Point]: ...

    def ping(self) -> None: ...


class CoordinateStore:
    """Coordinates per meter, fetched on demand and kept for `ttl` seconds (the portal has one `/geo` call each).

    Like the TTL cache, it serves what it has, flagged STALE, when the portal fails to refresh entries that are at
    most `max_stale` seconds past their TTL (coordinates hardly ever change, so that is nearly always right), and a
    failed refresh is not repeated for `failure_seconds`, so an outage does not make every caller wait out its own
    attempt. One refresh runs at a time: a caller that arrives meanwhile waits for it, then finds the data here.
    """

    def __init__(self, ttl: float, max_stale: float = 0.0, failure_seconds: float = FAILURE_MEMO_SECONDS) -> None:
        self._ttl = ttl
        self._max_stale = max_stale
        self._failure_seconds = failure_seconds
        self._lock = threading.Lock()
        self._items: dict[str, tuple[float, Point]] = {}
        self._failure: tuple[float, BaseException] | None = None  # (not before, what went wrong)

    def _servable(self, meter_id: str, now: float) -> bool:
        """Do we hold this meter's coordinates, and not so long ago that serving them would mislead?"""
        entry = self._items.get(meter_id)
        return entry is not None and now - entry[0] <= self._ttl + self._max_stale

    def _held(self, meter_ids: Sequence[str]) -> dict[str, Point]:
        return {i: self._items[i][1] for i in meter_ids if i in self._items}

    def get(
        self, meter_ids: Sequence[str], fetch: Callable[[list[str]], Mapping[str, Point]]
    ) -> tuple[dict[str, Point], CacheStatus]:
        with self._lock:
            now = time.monotonic()
            missing = [i for i in meter_ids if i not in self._items or now - self._items[i][0] > self._ttl]
            if not missing:
                return self._held(meter_ids), "HIT"
            failure = self._failure[1] if self._failure is not None and now < self._failure[0] else None
            if failure is None:
                try:
                    fetched = fetch(missing)
                except _STALE_ON as exc:
                    failure = exc
                    self._failure = (time.monotonic() + self._failure_seconds, exc)
                else:
                    fetched_at = time.monotonic()
                    for meter_id, point in fetched.items():
                        self._items[meter_id] = (fetched_at, point)
                    self._failure = None
                    return self._held(meter_ids), "MISS"
            # The portal failed just now, or a moment ago: serve what we hold if that is honest, else the failure.
            if not all(self._servable(i, time.monotonic()) for i in missing):
                raise copy.copy(failure)  # something we never had, or had too long ago
            return self._held(meter_ids), "STALE"


class ServiceState:
    """State that must outlive one request, kept once per portal client: the caches and the readiness memo."""

    def __init__(self, settings: Settings) -> None:
        self.cache = TTLCache(settings.cache_max_stale_seconds, stale_on=_STALE_ON)
        self.coordinates = CoordinateStore(settings.cache_ttl_geo_seconds, settings.cache_max_stale_seconds)
        self.ready_until = 0.0
        self.ready_lock = threading.Lock()


@dataclass(frozen=True)
class MeterQuery:
    """Everything `GET /api/v1/meters` can filter and shape by."""

    status: str | None = None
    q: str | None = None
    make: str | None = None
    phase: str | None = None
    dt: str | None = None
    hierarchy: Mapping[str, str] = field(default_factory=dict)  # level -> code, e.g. {"feeder": "F-001"}
    near: tuple[float, float, float] | None = None  # latitude, longitude, radius in metres
    include: frozenset[str] = frozenset()  # "coordinates" and/or "hierarchy"
    limit: int | None = None
    offset: int = 0


def _worst(*statuses: CacheStatus) -> CacheStatus:
    return max(statuses, key=_RANK.__getitem__)


class MeterService:
    def __init__(self, client: PortalSource, settings: Settings, state: ServiceState) -> None:
        self._client = client
        self._settings = settings
        self._state = state

    def _cached(self, key: str, ttl: float, loader: Callable[[], Any]) -> tuple[Any, CacheStatus]:
        return self._state.cache.get_or_load(key, ttl, loader)

    # -- Meters ---------------------------------------------------------------------

    def list_meters(self, query: MeterQuery) -> tuple[list[dict[str, Any]], int, CacheStatus]:
        """The requested page of meters, how many matched before paging, and where the data came from."""
        ttl = self._settings.cache_ttl_seconds
        meters, cache = self._cached("meters", ttl, self._client.get_meters)
        seen = [cache]
        rows = [m for m in meters if _matches_attributes(m, query)]

        paths: dict[str, list[dict[str, Any]]] = {}
        if query.hierarchy or "hierarchy" in query.include:
            paths, cache = self._cached("hierarchy:paths", ttl, self._client.get_hierarchy_paths)
            seen.append(cache)
            if query.hierarchy:
                rows = [m for m in rows if _in_branch(paths.get(m.get("dt_code") or ""), query.hierarchy)]

        points: dict[str, Point] = {}
        if query.near is not None:
            lat, lon, radius = query.near
            points, cache = self._coordinates([m["meter_id"] for m in rows])
            seen.append(cache)
            rows = _within_radius(rows, points, lat, lon, radius)

        total = len(rows)
        page = rows[query.offset : None if query.limit is None else query.offset + query.limit]

        if query.near is not None or "coordinates" in query.include:
            if query.near is None:  # a radius search already fetched everything it needs
                points, cache = self._coordinates([m["meter_id"] for m in page])
                seen.append(cache)
            page = [_with_point(m, points.get(m["meter_id"], (None, None))) for m in page]
        if "hierarchy" in query.include:
            page = [{**m, "hierarchy_path": _codes(paths.get(m.get("dt_code") or ""))} for m in page]
        return page, total, _worst(*seen)

    def _coordinates(self, meter_ids: Sequence[str]) -> tuple[dict[str, Point], CacheStatus]:
        return self._state.coordinates.get(meter_ids, lambda missing: self._client.get_coordinates(missing))

    def _resolve(self, meter_id: str) -> tuple[str, CacheStatus]:
        """The portal's own spelling of a meter id, taken from the cached list, or PortalNotFoundError.

        An id the list does not contain never reaches the portal's detail endpoints. What those answer
        for an unknown id was never observed, and this way an unknown id costs no upstream request.
        The price: a meter added within the last TTL window answers 404 until the list refreshes.
        """
        meters, cache = self._cached("meters", self._settings.cache_ttl_seconds, self._client.get_meters)
        wanted = meter_id.casefold()
        for meter in meters:
            if str(meter.get("meter_id", "")).casefold() == wanted:
                return str(meter["meter_id"]), cache
        raise PortalNotFoundError(f"meter {meter_id!r} does not exist")

    def get_meter(self, meter_id: str) -> tuple[dict[str, Any], CacheStatus]:
        canonical, listed = self._resolve(meter_id)
        detail, cache = self._cached(
            f"meter:{canonical}", self._settings.cache_ttl_seconds, lambda: self._client.get_meter_details(canonical)
        )
        return detail, _worst(listed, cache)

    def get_consumption(
        self, meter_id: str, start: datetime | None = None, end: datetime | None = None
    ) -> tuple[str, list[dict[str, Any]], CacheStatus]:
        """The meter's id, its readings (restricted to start..end if given) and where they came from.

        The portal takes no date range: it returns its whole window, so the range is applied here.
        """
        canonical, listed = self._resolve(meter_id)
        records, cache = self._cached(
            f"consumption:{canonical}",
            self._settings.cache_ttl_consumption_seconds,
            lambda: self._client.get_meter_consumption(canonical),
        )
        if start is not None or end is not None:
            records = [r for r in records if _within(r, start, end)]
        return canonical, records, _worst(listed, cache)

    # -- Network ---------------------------------------------------------------------

    def get_hierarchy(
        self, root: str | None = None, depth: int | None = None
    ) -> tuple[list[dict[str, Any]], CacheStatus]:
        """The tree, or the subtree under `root`, cut off `depth` levels below it."""
        tree, cache = self._cached("hierarchy", self._settings.cache_ttl_seconds, self._client.get_hierarchy)
        if root is not None:
            node = _find_node(tree, root)
            if node is None:
                raise PortalNotFoundError(f"hierarchy node {root!r} does not exist")
            tree = [node]
        if depth is not None:
            tree = [_prune(node, depth) for node in tree]
        return tree, cache

    def get_hierarchy_issues(self) -> tuple[dict[str, Any], CacheStatus]:
        report, cache = self._cached(
            "hierarchy:issues", self._settings.cache_ttl_seconds, self._client.get_hierarchy_issues
        )
        return report, cache

    def get_transformers(self) -> tuple[list[dict[str, Any]], CacheStatus]:
        ttl = self._settings.cache_ttl_seconds
        transformers, first = self._cached("transformers", ttl, self._client.get_transformers)
        meters, second = self._cached("meters", ttl, self._client.get_meters)
        counts = Counter(m.get("dt_code") for m in meters)
        return [{**t, "meter_count": counts.get(t["code"], 0)} for t in transformers], _worst(first, second)

    def get_summary(self) -> tuple[dict[str, Any], CacheStatus]:
        ttl = self._settings.cache_ttl_seconds
        meters, first = self._cached("meters", ttl, self._client.get_meters)
        transformers, second = self._cached("transformers", ttl, self._client.get_transformers)

        def counted(key: str) -> dict[str, int]:
            return dict(sorted(Counter(str(m.get(key) or "UNKNOWN") for m in meters).items()))

        summary = {
            "total_meters": len(meters),
            "by_status": counted("status"),
            "by_make": counted("make"),
            "by_phase": counted("phase_type"),
            "by_dt": counted("dt_code"),
            "transformers": len(transformers),
        }
        return summary, _worst(first, second)

    # -- Health ----------------------------------------------------------------------

    def check_portal(self) -> None:
        """Readiness: can we hold a session? Remembers a success for a few seconds."""
        state = self._state
        with state.ready_lock:  # concurrent probes share one portal round trip
            if time.monotonic() < state.ready_until:
                return
            self._client.ping()
            state.ready_until = time.monotonic() + READY_CACHE_SECONDS


# -- Pure helpers ------------------------------------------------------------------------


def _matches_attributes(meter: Mapping[str, Any], query: MeterQuery) -> bool:
    def is_(key: str, wanted: str | None) -> bool:
        if wanted is None or not wanted.strip():  # an empty filter (`?status=`) is no filter, like `?zone=`
            return True
        return str(meter.get(key) or "").casefold() == wanted.strip().casefold()

    if not (is_("status", query.status) and is_("make", query.make) and is_("phase_type", query.phase)):
        return False
    if not is_("dt_code", query.dt):
        return False
    if query.q:
        needle = query.q.casefold()
        haystacks = (str(meter.get("meter_id") or ""), str(meter.get("serial_number") or ""))  # None is not "None"
        return any(needle in text.casefold() for text in haystacks)
    return True


def _in_branch(path: Sequence[Mapping[str, Any]] | None, wanted: Mapping[str, str]) -> bool:
    """Does this breadcrumb pass through every requested (level, code)?"""
    if not path:
        return False
    return all(
        any(step.get("level") == level and str(step.get("code") or "").casefold() == code.casefold() for step in path)
        for level, code in wanted.items()
    )


def _with_point(meter: dict[str, Any], point: Point) -> dict[str, Any]:
    return {**meter, "latitude": point[0], "longitude": point[1]}


def _codes(path: Sequence[Mapping[str, Any]] | None) -> list[str] | None:
    return None if not path else [str(step["code"]) for step in path if step.get("code")]


def _within_radius(
    rows: Sequence[dict[str, Any]], points: Mapping[str, Point], lat: float, lon: float, radius_m: float
) -> list[dict[str, Any]]:
    """Meters within `radius_m` of the point, nearest first. A cheap bounding box goes before haversine."""
    south, north, west, east = bounding_box(lat, lon, radius_m)
    found: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        latitude, longitude = points.get(row["meter_id"], (None, None))
        if latitude is None or longitude is None:
            continue  # no usable coordinates: cannot be placed, so cannot be "near"
        if not (south <= latitude <= north and west <= longitude <= east):
            continue
        distance = haversine_m(lat, lon, latitude, longitude)
        if distance <= radius_m:
            found.append((distance, row))
    found.sort(key=lambda pair: pair[0])
    return [{**row, "distance_m": round(distance, 1)} for distance, row in found]


def _moment(record: Mapping[str, Any]) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(record["period"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _within(record: Mapping[str, Any], start: datetime | None, end: datetime | None) -> bool:
    moment = _moment(record)
    if moment is None:
        return True  # a label we cannot place in time is not filtered out by a time range
    return (start is None or moment >= start) and (end is None or moment <= end)


def interval_minutes(records: Sequence[Mapping[str, Any]]) -> int | None:
    """The spacing of the readings in minutes, if they are evenly spaced."""
    moments = [m for m in (_moment(r) for r in records) if m is not None]
    steps = {round((b - a).total_seconds() / 60) for a, b in pairwise(moments)}
    return steps.pop() if len(steps) == 1 else None


def _find_node(nodes: Sequence[dict[str, Any]], node_id: str) -> dict[str, Any] | None:
    for node in nodes:
        if str(node["node_id"]).casefold() == node_id.casefold():
            return node
        found = _find_node(node["children"], node_id)
        if found is not None:
            return found
    return None


def _prune(node: Mapping[str, Any], depth: int) -> dict[str, Any]:
    """A copy of `node` with its descendants cut off below `depth` levels (0 = the node alone)."""
    children = [] if depth <= 0 else [_prune(child, depth - 1) for child in node["children"]]
    return {**node, "children": children}
