"""Common missile launch areas and the regions they're typically used on.

Channels name the launch area of a ballistic / missile threat ("Загроза
балістики з Курська", "Курськ ОТРК!", "Воронеж загроза балістики!"). A
marker on Kursk alone is useless to someone in Kyiv - the point is the
possible trajectory *to them*.

Each area is used against *nearby* regions, not everything a missile could
technically reach: Crimea against Odesa / Mykolaiv / Kherson / Zaporizhzhia,
Voronezh against Sumy / Kharkiv / Poltava, Bryansk-Kursk-Belgorod against
the north, Kyiv included. ``reach_km`` encodes that - a target further away
gets no trajectory from that area.

  distance, km   Київ Чернігів Суми Харків Полтава Дніпро Запоріжжя Одеса Миколаїв Херсон
  Брянськ         407    285    261   384    406     534    603      796    717     745
  Курськ          421    340    133   193    265     373    439      706    610     623
  Бєлгород        429    383    130    72    183     262    324      629    525     528
  Воронеж         621    547    317   279    401     463    515      844    737     735
  Крим            641    725    625   539    477     355    288      314    262     203
"""

from __future__ import annotations

from dataclasses import dataclass

from .util import haversine_km


@dataclass(frozen=True, slots=True)
class LaunchSite:
    name: str
    lat: float
    lon: float
    radius_km: float           # a reported fix this close counts as this area
    reach_km: float            # targets it's typically used on are within this


LAUNCH_SITES: tuple[LaunchSite, ...] = (
    LaunchSite("Брянськ", 53.243, 34.363, 90, 450),
    LaunchSite("Курськ", 51.730, 36.193, 90, 450),
    LaunchSite("Бєлгород", 50.595, 36.587, 45, 450),    # Харків is ~72 km away
    LaunchSite("Воронеж", 51.660, 39.200, 120, 420),    # Суми/Харків/Полтава, not Київ
    LaunchSite("Крим", 45.300, 34.400, 130, 400),       # the south; Херсон is ~200 km
)

# missile threats stay on the map (and keep polling fast) this long after
# their last report, whatever the time window, unless resolved or they hit
THREAT_HOLD_MIN = {"ballistic": 45, "cruise": 90}

# average speed over the whole flight: an Iskander covers ~400 km in 4-5
# minutes; a Zircon is in the same class
BALLISTIC_AVG_KMH = 6000.0
HYPERSONIC_AVG_KMH = 4500.0


def site_at(lat: float | None, lon: float | None) -> LaunchSite | None:
    """The launch area a position belongs to, if any."""
    if lat is None or lon is None:
        return None
    best = None
    for s in LAUNCH_SITES:
        d = haversine_km((lat, lon), (s.lat, s.lon))
        if d <= s.radius_km and (best is None or d < best[0]):
            best = (d, s)
    return best[1] if best else None


def reaches(site: LaunchSite, target: tuple[float, float]) -> bool:
    """Is ``target`` among the regions this area is typically used on?"""
    return haversine_km((site.lat, site.lon), target) <= site.reach_km


def candidates(target: tuple[float, float]) -> list[LaunchSite]:
    """Usual launch areas a ballistic missile on ``target`` may come from."""
    return [s for s in LAUNCH_SITES if reaches(s, target)]
