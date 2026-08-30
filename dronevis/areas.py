"""Areas of interest. Everything is stored regardless; areas only drive what
the UI shows and which centre the resolver uses to break ambiguous names."""

from __future__ import annotations

from .config import Area, Config
from .geo.util import haversine_km


def area_center(area: Area) -> tuple[float, float]:
    if area.center is not None:
        return area.center
    if area.bbox is not None:
        s, w, n, e = area.bbox
        return ((s + n) / 2, (w + e) / 2)
    return (50.4501, 30.5234)


def in_area(area: Area, lat: float | None, lon: float | None) -> bool:
    if lat is None or lon is None:
        return False
    if area.bbox is not None:
        s, w, n, e = area.bbox
        return s <= lat <= n and w <= lon <= e
    if area.center is not None and area.radius_km is not None:
        return haversine_km((lat, lon), area.center) <= area.radius_km
    return True


def resolve_area(cfg: Config, key: str | None) -> Area | None:
    """``None`` / 'all' -> no filter. Otherwise the named area (falling back to
    the configured default)."""
    if key in (None, "", "all"):
        return None
    return cfg.areas.defined.get(key) or cfg.default_area
