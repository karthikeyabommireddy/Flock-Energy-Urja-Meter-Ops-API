"""Great-circle distance and a cheap bounding box for 'meters near this point' queries. Pure functions."""

from __future__ import annotations

import math

EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres; plenty accurate for 'meters within 500 m of here'."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def bounding_box(lat: float, lon: float, radius_m: float) -> tuple[float, float, float, float]:
    """`(min_lat, max_lat, min_lon, max_lon)` of a box that contains the whole circle around the point.

    Used to discard most points with plain comparisons before paying for haversine. Where the circle reaches a
    pole or crosses the antimeridian the longitude range widens to the whole globe, so the box is always a
    superset of the circle and never drops a point that is really inside it.
    """
    r = radius_m / EARTH_RADIUS_M
    phi = math.radians(lat)
    min_lat, max_lat = math.degrees(phi - r), math.degrees(phi + r)
    if min_lat <= -90.0 or max_lat >= 90.0:
        return max(min_lat, -90.0), min(max_lat, 90.0), -180.0, 180.0
    dlon = math.degrees(math.asin(math.sin(r) / math.cos(phi)))
    min_lon, max_lon = lon - dlon, lon + dlon
    if min_lon < -180.0 or max_lon > 180.0:
        return min_lat, max_lat, -180.0, 180.0
    return min_lat, max_lat, min_lon, max_lon
