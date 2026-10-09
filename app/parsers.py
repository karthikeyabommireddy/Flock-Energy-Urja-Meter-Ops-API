"""Portal payloads -> plain dictionaries. Pure functions (no I/O): every quirk found in the recon is handled here.

What each parser reads is documented in PROTOCOL.md. A structural surprise (a missing key, a shape never seen
before) raises `PortalParseError`: an empty or half-filled answer would be a silent lie. A single bad value (one
unparseable number) degrades to `None` with a warning instead.
"""

from __future__ import annotations

import json
import logging
import math
from collections import Counter
from datetime import datetime, timedelta
from itertools import pairwise
from typing import Any
from zoneinfo import ZoneInfo

from app.errors import PortalNotFoundError, PortalParseError
from app.normalize import clean_text, parse_float, parse_portal_timestamp, split_label, to_rfc3339

logger = logging.getLogger("urja_client")

#: The portal's breadcrumb levels, top to bottom: our key, and the labels the portal has used for it.
HIERARCHY_LEVELS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("zone", ("Zone",)),
    ("circle", ("Circle",)),
    ("division", ("Division",)),
    ("subdivision", ("Subdivision",)),
    ("substation", ("Sub Station", "Substation")),
    ("feeder", ("Feeder",)),
    ("dt", ("DT",)),
)

#: Meter detail comes in two shapes (see `_detail_parameters`); both are normalised to these labels.
_DETAIL_KEYS = {
    "Meter ID": "meter_id",
    "Serial No": "serial_number",
    "Make": "make",
    "Phase Type": "phase_type",
    "Installation Status": "status",
    "Installation Type": "installation_type",
}
_CLASS_DATA_KEYS = {
    "MeterId": "Meter ID",
    "SerialNo": "Serial No",
    "Make": "Make",
    "PhaseType": "Phase Type",
    "InstallationStatus": "Installation Status",
    "InstallationType": "Installation Type",
}


# -- SvelteKit's data format ---------------------------------------------------------------


#: A meter page holds a few dozen values. Anything wildly larger is not one, and is refused rather than expanded.
_MAX_DEVALUE_VALUES = 100_000
#: A real page nests a handful of levels. This stops a cyclic or hostile payload long before Python's recursion limit.
_MAX_DEVALUE_DEPTH = 100


def unflatten(values: list[Any]) -> Any:
    """Decode SvelteKit's `devalue` encoding.

    The payload is one flat list. `values[0]` is the root. Objects and arrays hold *indices* into the same
    list instead of nested values, so a repeated value is stored once. Negative indices are the constants
    undefined (-1), a hole in a sparse array (-2), NaN (-3), Infinity (-4), -Infinity (-5) and -0 (-6): the
    numbering of devalue 4.3 and later, which SvelteKit 2 ships (older versions had no hole and numbered NaN
    as -2; the captured payloads only use -1). A list whose first element is a string is a tagged special
    type such as ["Date", "2026-01-01T00:00:00.000Z"]. Anything malformed, oversized or too deeply nested
    raises PortalParseError, never another exception and never an unbounded amount of work.
    """
    if not values:
        raise PortalParseError("empty devalue payload")
    if len(values) > _MAX_DEVALUE_VALUES:
        raise PortalParseError(f"devalue payload has {len(values)} values; refusing to expand it")
    constants = {-1: None, -2: None, -3: math.nan, -4: math.inf, -5: -math.inf, -6: -0.0}
    memo: dict[int, Any] = {}
    depth = 0

    def hydrate(index: Any) -> Any:
        nonlocal depth
        if not isinstance(index, int) or isinstance(index, bool):
            raise PortalParseError(f"devalue reference is not an integer: {str(index)[:40]!r}")
        if index < 0:
            return constants.get(index)
        if index >= len(values):
            raise PortalParseError(f"devalue reference {index} is outside the payload")
        if index in memo:
            return memo[index]
        depth += 1
        if depth > _MAX_DEVALUE_DEPTH:
            raise PortalParseError("devalue payload is nested too deeply")
        try:
            return decode(index, values[index])
        finally:
            depth -= 1

    def decode(index: int, value: Any) -> Any:
        if isinstance(value, dict):
            record: dict[str, Any] = {}
            memo[index] = record  # registered before recursing, so a cycle terminates
            for key, ref in value.items():
                record[key] = hydrate(ref)
            return record
        if isinstance(value, list):
            if value and isinstance(value[0], str):
                return decode_tagged(index, value)
            items: list[Any] = []
            memo[index] = items
            items.extend(hydrate(ref) for ref in value)
            return items
        memo[index] = value  # strings, numbers, booleans and null are stored as they are
        return value

    def decode_tagged(index: int, value: list[Any]) -> Any:
        tag, rest = value[0], value[1:]
        if tag == "Set":
            members: list[Any] = []
            memo[index] = members  # containers are registered before their members, like objects and arrays
            members.extend(hydrate(ref) for ref in rest)
            return members
        if tag == "Map":  # [tag, keyIndex, valueIndex, keyIndex, valueIndex, ...]
            mapping: dict[Any, Any] = {}
            memo[index] = mapping
            for i in range(0, len(rest) - 1, 2):
                key = hydrate(rest[i])
                if not isinstance(key, str | int | float | bool | type(None)):
                    raise PortalParseError("devalue Map with a key that cannot be a dictionary key")
                mapping[key] = hydrate(rest[i + 1])
            return mapping
        if tag == "null":  # an object without a prototype: [tag, "key", valueIndex, "key", valueIndex, ...]
            plain: dict[str, Any] = {}
            memo[index] = plain
            for i in range(0, len(rest) - 1, 2):
                plain[str(rest[i])] = hydrate(rest[i + 1])
            return plain
        # The remaining tags carry their payload inline, not as an index into the list.
        result: Any
        if tag in ("Date", "RegExp", "Object"):
            result = rest[0]
        elif tag == "BigInt":
            result = int(rest[0])
        else:
            raise PortalParseError(f"unknown devalue type {str(tag)[:40]!r}")
        memo[index] = result
        return result

    try:
        return hydrate(0)
    except (IndexError, KeyError, TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise PortalParseError(f"malformed devalue payload ({type(exc).__name__})") from exc


def parse_sveltekit_node(payload: Any) -> Any:
    """`GET .../__data.json` -> the decoded data of the page's own node.

    The response is `{"type": "data", "nodes": [...]}` with one node per layout/page; nodes we did not ask for
    are `{"type": "skip"}`. The node that carries data is the one we want.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("nodes"), list):
        raise PortalParseError("not a SvelteKit data response (no 'nodes')")
    for node in reversed(payload["nodes"]):
        if not isinstance(node, dict):
            continue
        kind = node.get("type")
        if kind == "data":
            data = node.get("data")
            if not isinstance(data, list):
                raise PortalParseError("a data node without a data array")
            return unflatten(data)
        if kind == "error":
            if node.get("status") == 404:
                raise PortalNotFoundError("the portal reports this page as not found")
            raise PortalParseError(f"the portal returned an error node (status {node.get('status')})")
    raise PortalParseError("no data node in the response")


# -- Meter detail ----------------------------------------------------------------------------


def _detail_parameters(detail: Any) -> dict[str, Any]:
    """Both detail shapes seen in the portal -> {'Meter ID': ..., 'Serial No': ..., ...}.

    Shape A (most meters): {"data": [{"parameterName": "Make", "parameterValue": "HPL"}, ...]}.
    Shape B (some meters): {"classData": "<JSON text>"}, where the text holds
    {"installed_meter": {"MeterId": ..., "SerialNo": ..., "Make": ...}} with PascalCase keys.
    """
    if isinstance(detail, dict) and isinstance(detail.get("data"), list):
        return {
            str(item["parameterName"]): item.get("parameterValue")
            for item in detail["data"]
            if isinstance(item, dict) and "parameterName" in item
        }
    if isinstance(detail, dict) and isinstance(detail.get("classData"), str):
        try:
            inner = json.loads(detail["classData"])["installed_meter"]
        except (ValueError, KeyError, TypeError) as exc:
            raise PortalParseError("meter detail 'classData' is not the JSON we expect") from exc
        if not isinstance(inner, dict):
            raise PortalParseError("meter detail 'classData.installed_meter' is not an object")
        return {_CLASS_DATA_KEYS.get(str(key), str(key)): value for key, value in inner.items()}
    raise PortalParseError("meter detail in a shape we have not seen")


def hierarchy_levels(hierarchy: dict[str, Any]) -> list[dict[str, str | None]]:
    """The breadcrumb object -> [{'level': 'zone', 'name': 'Jaipur Zone 2', 'code': 'Z-02'}, ...] top to bottom.

    A level the portal leaves out is simply absent here; the hierarchy audit reports it, nothing is invented.
    """
    levels: list[dict[str, str | None]] = []
    for level, aliases in HIERARCHY_LEVELS:
        label = next((hierarchy[alias] for alias in aliases if alias in hierarchy), None)
        name, code = split_label(label)
        if name is None and code is None:
            continue
        levels.append({"level": level, "name": name, "code": code})
    return levels


def parse_meter_detail(payload: Any) -> dict[str, Any]:
    """`GET /meters/<id>/__data.json` -> the meter's nameplate and its place in the network."""
    data = parse_sveltekit_node(payload)
    if not isinstance(data, dict):
        raise PortalParseError("meter page data is not an object")
    params = _detail_parameters(data.get("detail"))
    if "hierarchy" not in data:
        raise PortalParseError("meter page data without a 'hierarchy' entry")  # the layout changed: not "no breadcrumb"
    raw_hierarchy = data["hierarchy"]
    if raw_hierarchy is not None and not isinstance(raw_hierarchy, dict):
        raise PortalParseError("meter page 'hierarchy' is not an object")
    hierarchy: dict[str, Any] = raw_hierarchy or {}  # an explicit null: the portal says the meter has no breadcrumb
    levels = hierarchy_levels(hierarchy)

    meter_id = clean_text(params.get("Meter ID")) or clean_text(data.get("meterId"))
    if meter_id is None:
        raise PortalParseError("meter detail without a meter id")
    if clean_text(data.get("meterId")) not in (None, meter_id):
        logger.warning("parse.detail.id_mismatch page=%r detail=%r", data.get("meterId"), meter_id)

    status = clean_text(params.get("Installation Status") or hierarchy.get("Installation Status"))
    dt = next((level for level in levels if level["level"] == "dt"), None)
    return {
        "meter_id": meter_id,
        "serial_number": clean_text(params.get("Serial No")),
        "status": status.upper() if status else None,
        "make": clean_text(params.get("Make")),
        "phase_type": clean_text(params.get("Phase Type")),
        "installation_type": clean_text(params.get("Installation Type") or hierarchy.get("Installation Type")),
        "dt_code": dt["code"] if dt else None,
        # The portal has no place name for a meter; the transformer it hangs off is the closest thing.
        "location": dt["name"] if dt else None,
        "installed_date": None,  # the portal shows no installation date
        "hierarchy": levels,
        "latitude": None,
        "longitude": None,
        "raw_fields": {
            key: text
            for key, value in params.items()
            if key not in _DETAIL_KEYS and (text := clean_text(value)) is not None
        },
    }


def parse_geo(payload: Any) -> tuple[float | None, float | None]:
    """`GET /portal/meters/<id>/geo` -> (latitude, longitude) as floats; (None, None) if unusable.

    The portal sends the coordinates as strings. Out-of-range values and the 0,0 "no fix" placeholder become
    None instead of a point in the Gulf of Guinea.
    """
    if not isinstance(payload, dict) or "data" not in payload:
        raise PortalParseError("geo payload without 'data'")
    data = payload["data"]
    if data is None:
        return None, None
    if not isinstance(data, dict):
        raise PortalParseError("geo 'data' is not an object")
    try:
        latitude, longitude = parse_float(data.get("latitude")), parse_float(data.get("longitude"))
    except ValueError:
        logger.warning("parse.geo.not_a_number raw=%r", data)
        return None, None
    if latitude is None or longitude is None:
        return None, None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180) or (latitude == 0 and longitude == 0):
        logger.warning("parse.geo.unusable latitude=%s longitude=%s", latitude, longitude)
        return None, None
    return latitude, longitude


# -- Consumption -----------------------------------------------------------------------------


def _number(row: dict[str, Any], key: str) -> float | None:
    try:
        return parse_float(row.get(key))
    except ValueError:
        logger.warning("parse.energy.not_a_number key=%s raw=%r", key, row.get(key))
        return None


def parse_energy(payload: Any, tz: ZoneInfo) -> list[dict[str, Any]]:
    """`GET /portal/meters/<id>/energy` -> readings oldest first.

    Every value arrives as a string, and `kwh` / `kvah` are *cumulative registers*, not the consumption of
    the interval. `value` (unit kWh) keeps the register reading; `interval_kwh` is the difference to the
    reading one step earlier. It is None for the first reading, wherever the previous reading is missing or
    unreadable (the difference would then span several intervals) and wherever the register falls (a reset,
    not consumption). Gaps are left as gaps: nothing is interpolated.
    """
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise PortalParseError("energy payload without a 'data' list")
    readings: dict[Any, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise PortalParseError("energy row is not an object")
        stamp = clean_text(row.get("timestamp"))
        try:
            moment = parse_portal_timestamp(stamp, tz) if stamp else None
        except ValueError:
            moment = None
        if moment is None:
            logger.warning("parse.energy.bad_timestamp raw=%r", row.get("timestamp"))
            continue  # a reading we cannot place on the time axis is skipped, not guessed
        if moment in readings:
            logger.warning("parse.energy.duplicate_timestamp raw=%r", row.get("timestamp"))
            continue
        readings[moment] = {
            "moment": moment,
            "kwh": _number(row, "kwh"),
            "kvah": _number(row, "kvah"),
            "voltage_r": _number(row, "voltR"),
        }
    if rows and not readings:
        raise PortalParseError(f"none of the {len(rows)} energy rows had a timestamp we can read")

    # The series' usual spacing (30 minutes in the capture). A reading is "one step after" the previous one
    # if it is within one and a half steps: that tolerates jitter but not a missing reading.
    moments = sorted(readings)
    spacings = Counter(later - earlier for earlier, later in pairwise(moments))
    step = spacings.most_common(1)[0][0] if spacings else timedelta(minutes=30)

    series: list[dict[str, Any]] = []
    previous: tuple[datetime, float | None] | None = None
    for moment in moments:
        reading = readings[moment]
        interval = None
        if (
            previous is not None
            and previous[1] is not None
            and reading["kwh"] is not None
            and reading["kwh"] >= previous[1]
            and moment - previous[0] <= step * 1.5
        ):
            interval = round(reading["kwh"] - previous[1], 4)
        previous = (moment, reading["kwh"])  # even an unreadable value: the next interval must not reach past it
        series.append(
            {
                "period": to_rfc3339(moment),
                "value": reading["kwh"],
                "unit": "kWh",
                "kvah": reading["kvah"],
                "voltage_r": reading["voltage_r"],
                "interval_kwh": interval,
            }
        )
    return series


# -- Lists -----------------------------------------------------------------------------------


def _paging(payload: dict[str, Any]) -> tuple[int, int, int]:
    try:
        return int(payload["total"]), int(payload["page"]), int(payload["pageSize"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PortalParseError("paged payload without total/page/pageSize") from exc


def parse_meter_page(payload: Any) -> tuple[list[dict[str, Any]], int, int, int]:
    """One page of `GET /portal/meters/search` -> (rows, total, page, page_size)."""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise PortalParseError("meter search payload without a 'data' list")
    total, page, page_size = _paging(payload)
    rows: list[dict[str, Any]] = []
    for item in payload["data"]:
        if not isinstance(item, dict):
            raise PortalParseError("meter row is not an object")
        meter_id = clean_text(item.get("meterId"))
        if meter_id is None:
            logger.warning("parse.meter.row_without_id row=%r", item)
            continue
        status = clean_text(item.get("installStatus"))
        rows.append(
            {
                "meter_id": meter_id,
                "serial_number": clean_text(item.get("serialNo")),
                "status": status.upper() if status else None,
                "location": None,  # the list carries no place; see parse_meter_detail
                "make": clean_text(item.get("make")),
                "phase_type": clean_text(item.get("phaseType")),
                "dt_code": clean_text(item.get("dtCode")),
            }
        )
    if payload["data"] and not rows:
        raise PortalParseError(f"none of the {len(payload['data'])} meter rows carried a meter id")
    return rows, total, page, page_size


def parse_dt_page(payload: Any) -> tuple[list[dict[str, Any]], int, int, int]:
    """One page of `GET /portal/dts` (the Transformers screen) -> (rows, total, page, page_size)."""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise PortalParseError("transformer payload without a 'data' list")
    total, page, page_size = _paging(payload)
    rows: list[dict[str, Any]] = []
    for item in payload["data"]:
        if not isinstance(item, dict):
            raise PortalParseError("transformer row is not an object")
        code = clean_text(item.get("code"))
        if code is None:
            logger.warning("parse.dt.row_without_code row=%r", item)
            continue
        try:
            capacity = parse_float(item.get("capacityKva"))
        except ValueError:
            logger.warning("parse.dt.capacity_not_a_number raw=%r", item.get("capacityKva"))
            capacity = None
        rows.append(
            {
                "code": code,
                "name": clean_text(item.get("name")),
                "feeder_code": clean_text(item.get("feederCode")),
                "capacity_kva": capacity,
            }
        )
    if payload["data"] and not rows:
        raise PortalParseError(f"none of the {len(payload['data'])} transformer rows carried a code")
    return rows, total, page, page_size
