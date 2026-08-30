"""Spherical helpers."""

from __future__ import annotations

import math

EARTH_R_KM = 6371.0088


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_R_KM * math.asin(math.sqrt(h))


def bearing_deg(frm: tuple[float, float], to: tuple[float, float]) -> float:
    """Initial great-circle bearing frm -> to, degrees clockwise from north."""
    lat1, lon1 = map(math.radians, frm)
    lat2, lon2 = map(math.radians, to)
    dlon = lon2 - lon1
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


_COMPASS = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
]


def compass(deg: float | None) -> str | None:
    if deg is None:
        return None
    return _COMPASS[int((deg % 360) / 22.5 + 0.5) % 16]
