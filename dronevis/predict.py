"""Short-range motion prediction and danger assessment for one track.

Reports are coarse - a place name every few minutes, usually a town centre,
posted with a delay - so this is dead reckoning with honest error bars, not
a forecast:

* heading: the stated destination if there is one; else a recency-weighted
  mean of the last few observed legs (one jittery leg no longer swings it);
  else the compass course from the text
* speed: the observed ground speed over those legs when they span enough
  time, clamped to a plausible band around the type's cruise speed (report
  delays make raw values wild); else the cruise speed
* spread: half-angle of the heading uncertainty - wider the less we know
* horizon: how far ahead a drawn projection still means something for this
  type / status (0 = don't draw one: circling, impact, ballistic, aircraft)

* altitude: when channels post it ("400м", "3,2км"), a clear drop across a
  track's reports marks it descending even without the word "зниження"

``assess`` then answers "how worried should someone at this point be":
distance now, whether the projected course passes close by, when, and a
high / medium / low level.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from dateutil import parser as dtp

from .geo.util import EARTH_R_KM, bearing_deg, dest_point, haversine_km
from .parse.threats import FAMILY, MAX_SPEED_KMH, SPEED_KMH

MIN_LEG_KM = 4.0                 # shorter hops are place-centre jitter, not motion
MAX_LEGS = 3
LEG_WEIGHTS = (1.0, 0.6, 0.35)   # newest leg first
MIN_SPAN_MIN = 3.0               # observed speed needs at least this much time
SPEED_BAND = (0.5, 1.5)          # observed speed clamped to this x cruise speed
                                 # (or up to the type's top speed, if higher)

SPREAD_DEST = 8.0                # heading to a named target
SPREAD_ONE_LEG = 25.0
SPREAD_REPORTED = 30.0           # "курсом на північ" - an eight-point compass
SPREAD_MIN, SPREAD_MAX = 12.0, 45.0

# minutes ahead worth drawing, per threat family
HORIZON_MIN = {"drone": 20, "unknown": 15, "cruise": 10, "bomb": 6,
               "ballistic": 0, "aircraft": 0}
# ... and never further ahead than this: a 20-minute wedge for a 560 km/h jet
# UAV is 190 km of noise across the whole map
MAX_AHEAD_KM = 60.0
# statuses where the object isn't (meaningfully) flying a course
STATIONARY = {"circling", "impact", "clear"}

NEAR_KM = 10.0                   # this close is high danger whatever the course
CLOSE_KM = 30.0                  # this close and not moving away: at least medium
# ... except drones: around a big city there's nearly always one within 30 km,
# which would keep the danger amber all night - for them only really close
CLOSE_KM_BY_FAMILY = {"drone": 5.0, "unknown": 5.0}
DIVE_NEAR_KM = 25.0              # descending this close is high danger too
ALT_DROP = 0.7                   # latest altitude <= 70% of the previous = descending
ON_COURSE_KM = 10.0              # a pass this close counts as "toward you"


@dataclass(slots=True)
class Fix:
    t: datetime
    lat: float
    lon: float
    alt_m: float | None = None


@dataclass(slots=True)
class Motion:
    fix: Fix | None              # latest known position and when
    heading_deg: float | None
    heading_src: str | None      # "dest" | "observed" | "reported"
    spread_deg: float
    speed_kmh: float
    speed_observed: bool
    moving: bool                 # flying a course (heading known, not circling)
    horizon_min: float           # how far ahead to draw; 0 = don't
    dest: tuple[float, float] | None = None
    descending: bool = False     # said so, or its reported altitude is dropping
    alt_trend: str | None = None  # "down" | "up" from the last two altitudes
    status: str = "unknown"
    family: str = "unknown"

    def to_dict(self) -> dict:
        return {
            "heading_deg": None if self.heading_deg is None else round(self.heading_deg, 1),
            "heading_src": self.heading_src,
            "spread_deg": round(self.spread_deg, 1),
            "speed_kmh": round(self.speed_kmh),
            "speed_observed": self.speed_observed,
            "horizon_min": self.horizon_min,
            "fix_at": self.fix.t.isoformat() if self.fix else None,
            "descending": self.descending,
            "alt_trend": self.alt_trend,
        }


def _circular_mean(bearings: list[float], weights: list[float]) -> tuple[float, float]:
    """Weighted mean bearing and resultant length R (1 = all agree, 0 = none)."""
    x = sum(w * math.sin(math.radians(b)) for b, w in zip(bearings, weights))
    y = sum(w * math.cos(math.radians(b)) for b, w in zip(bearings, weights))
    r = math.hypot(x, y) / sum(weights)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0, r


def estimate_motion(
    fixes: list[Fix], *, family: str, status: str, cruise_kmh: float,
    reported_heading: float | None = None,
    dest: tuple[float, float] | None = None,
    max_kmh: float | None = None,
) -> Motion:
    fixes = sorted(fixes, key=lambda f: f.t)
    # collapse repeats of one place: keep when it was first seen there (for
    # leg timing) and the latest report from there (the freshest fix)
    pts: list[list] = []                       # [lat, lon, t_first, t_last]
    for f in fixes:
        if pts and (pts[-1][0], pts[-1][1]) == (f.lat, f.lon):
            pts[-1][3] = f.t
        else:
            pts.append([f.lat, f.lon, f.t, f.t])
    if not pts:
        return Motion(None, None, None, SPREAD_MAX, cruise_kmh, False, False, 0, dest)
    last = pts[-1]
    fix = Fix(last[3], last[0], last[1])

    # observed legs, newest first, skipping jitter shorter than MIN_LEG_KM
    legs: list[tuple[float, float, float]] = []      # (bearing, km, minutes)
    j = len(pts) - 1
    i = j - 1
    while i >= 0 and len(legs) < MAX_LEGS:
        a, b = (pts[i][0], pts[i][1]), (pts[j][0], pts[j][1])
        km = haversine_km(a, b)
        if km >= MIN_LEG_KM:
            mins = (pts[j][2] - pts[i][2]).total_seconds() / 60.0
            legs.append((bearing_deg(a, b), km, mins))
            j = i
        i -= 1

    obs_heading = None
    obs_spread = SPREAD_MAX
    if legs:
        w = list(LEG_WEIGHTS[:len(legs)])
        obs_heading, r = _circular_mean([lg[0] for lg in legs], w)
        obs_spread = (SPREAD_ONE_LEG if len(legs) == 1 else
                      min(SPREAD_MAX, max(SPREAD_MIN, SPREAD_MIN + (1.0 - r) * 120.0)))

    speed, observed = cruise_kmh, False
    span = sum(lg[2] for lg in legs)
    if legs and span >= MIN_SPAN_MIN:
        raw = sum(lg[1] for lg in legs) / (span / 60.0)
        lo, hi = SPEED_BAND
        top = max(cruise_kmh * hi, max_kmh or 0.0)
        speed = min(top, max(cruise_kmh * lo, raw))
        observed = True

    heading, src, spread = None, None, SPREAD_MAX
    if dest is not None and haversine_km((fix.lat, fix.lon), dest) >= 3.0:
        heading, src, spread = bearing_deg((fix.lat, fix.lon), dest), "dest", SPREAD_DEST
    elif obs_heading is not None:
        heading, src, spread = obs_heading, "observed", obs_spread
    elif reported_heading is not None:
        heading, src, spread = reported_heading, "reported", SPREAD_REPORTED

    # altitude trend from the last two reports that gave one
    alts = [f.alt_m for f in fixes if f.alt_m is not None]
    trend = None
    if len(alts) >= 2 and alts[-1] != alts[-2]:
        trend = "down" if alts[-1] < alts[-2] else "up"
    descending = status == "descending" or (
        trend == "down" and alts[-1] <= alts[-2] * ALT_DROP)

    moving = heading is not None and status not in STATIONARY
    horizon = float(HORIZON_MIN.get(family, 15)) if moving and status != "launch" else 0.0
    horizon = round(min(horizon, MAX_AHEAD_KM / max(speed, 1.0) * 60.0), 1)
    if descending:
        horizon = min(horizon, 2.0)          # it's coming down, not flying on
    return Motion(fix, heading, src, spread, speed, observed, moving, horizon, dest,
                  descending, trend, status, family)


def cluster_motion(c, evs, speed_kmh: dict[str, float]):
    """Motion estimate for a cluster row from its events (any order)."""
    fixes = []
    for e in flyable(evs, c["threat_type"], speed_kmh):
        try:
            alt = e["altitude_m"] if "altitude_m" in e.keys() else None
            fixes.append(Fix(dtp.isoparse(e["posted_at"]), e["lat"], e["lon"], alt))
        except (TypeError, ValueError):
            continue
    slug = c["threat_type"]
    return estimate_motion(
        fixes, family=FAMILY.get(slug, "unknown"), status=c["status"],
        cruise_kmh=speed_kmh.get(slug) or SPEED_KMH.get(slug, 200.0),
        reported_heading=c["heading_deg"],
        dest=(c["dest_lat"], c["dest_lon"]) if c["dest_lat"] is not None else None,
        max_kmh=MAX_SPEED_KMH.get(slug),
    )


def _dest_km(m: Motion) -> float | None:
    if m.dest is None or m.heading_src != "dest":
        return None
    return haversine_km((m.fix.lat, m.fix.lon), m.dest)


def dead_reckon(m: Motion, now: datetime) -> tuple[float, float] | None:
    """Estimated position *now*, while that's still within the horizon."""
    if not m.horizon_min or m.fix is None:
        return None
    mins = (now - m.fix.t).total_seconds() / 60.0
    if mins < 1.0 or mins > m.horizon_min:
        return None
    km = m.speed_kmh * mins / 60.0
    stop = _dest_km(m)
    if stop is not None:
        km = min(km, stop)
    return dest_point(m.fix.lat, m.fix.lon, m.heading_deg, km)


def cone(m: Motion, steps: int = 6) -> list[tuple[float, float]]:
    """Polygon of where it can plausibly be within the horizon: a wedge from
    the last fix, heading +/- spread, as far as speed x horizon (or the
    stated target, whichever is nearer)."""
    if not m.horizon_min or m.fix is None:
        return []
    km = m.speed_kmh * m.horizon_min / 60.0
    stop = _dest_km(m)
    if stop is not None:
        km = min(km, stop)
    origin = (m.fix.lat, m.fix.lon)
    arc = [dest_point(origin[0], origin[1],
                      m.heading_deg - m.spread_deg + 2 * m.spread_deg * k / steps, km)
           for k in range(steps + 1)]
    return [origin, *arc]


def _level(dist: float, toward: bool, eta: float | None, moving: bool,
           reach_min: float, descending: bool = False, receding: bool = False,
           family: str = "unknown") -> str:
    if dist <= NEAR_KM or (descending and dist <= DIVE_NEAR_KM):
        return "high"
    # close to home and not leaving: worth more than "low" whatever the course
    close = dist <= CLOSE_KM_BY_FAMILY.get(family, CLOSE_KM) and not receding
    if toward and eta is not None:
        if eta <= 15:
            return "high"
        if eta <= 45:
            return "medium"
        return "medium" if close else "low"
    # direction unknown: close in *time* is a worry (500 km is minutes for a
    # ballistic missile, an hour for a Shahed)
    if (not moving and reach_min <= 15) or close:
        return "medium"
    return "low"


def assess(m: Motion, ref: tuple[float, float], now: datetime,
           max_age_min: float | None = None) -> dict | None:
    """Danger of this track to the point ``ref``. A track with no report for
    ``max_age_min``, or one that already hit / was cleared, is history: its
    distance is still given, but it's never "toward" you or above "low"."""
    if m.fix is None:
        return None
    pos = dead_reckon(m, now) or (m.fix.lat, m.fix.lon)
    dist = haversine_km(ref, pos)
    v = max(1.0, m.speed_kmh) / 60.0                 # km per minute
    toward, receding, cpa, eta = False, False, None, None
    age = (now - m.fix.t).total_seconds() / 60.0
    over = (m.status in ("impact", "clear")
            or (max_age_min is not None and age > max_age_min))

    if m.moving and not over:
        # local flat frame centred on ref (fine at these ranges): x east, y north
        kx = math.radians(1) * EARTH_R_KM * math.cos(math.radians(ref[0]))
        ky = math.radians(1) * EARTH_R_KM
        px, py = (pos[1] - ref[1]) * kx, (pos[0] - ref[0]) * ky
        ux, uy = math.sin(math.radians(m.heading_deg)), math.cos(math.radians(m.heading_deg))
        along = -(px * ux + py * uy)                 # km until closest approach
        if along > 0:
            stop = _dest_km(m)
            if stop is not None:                     # it ends at its target
                done = haversine_km((m.fix.lat, m.fix.lon), pos)
                along = min(along, max(0.0, stop - done))
            cpa = math.hypot(px + ux * along, py + uy * along)
            eta = along / v
            # the further ahead, the wider the course can stray
            slack = max(ON_COURSE_KM, along * math.sin(math.radians(m.spread_deg)))
            toward = cpa <= slack
        else:
            cpa, receding = dist, True                # moving away

    return {
        "distance_km": round(dist, 1),
        "bearing_deg": round(bearing_deg(ref, pos)),
        "toward": toward,
        "cpa_km": None if cpa is None else round(cpa, 1),
        "eta_min": None if eta is None or not toward else round(eta, 1),
        "receding": receding,
        "stale": over,
        "level": "low" if over else _level(dist, toward, eta, m.moving, dist / v,
                                           m.descending, receding, m.family),
    }


# ---------------------------------------------------------------- life
# A threat is alive only while someone keeps reporting it: a drone can't sit
# still, so a track nobody has mentioned for a while is somewhere else by now
# (or down). Its last spot then only misleads - the "ghost" left on the map
# when one drone stops being reported and the next one shows up from the same
# place. Minutes without a report after which a track is gone, per family:
LIFE_MIN = {
    "drone": 25.0,       # Shahed ~170 km/h: 70 km off its last fix by then
    "cruise": 20.0,      # matches the launch-area hold (geo/launch.py)
    "ballistic": 7.0,    # launch to hit in ~5 min, plus posting lag
    "bomb": 15.0,        # a KAB glides for minutes, not hours
    "aircraft": 60.0,
    "unknown": 25.0,     # an untyped sighting is in practice a drone
}


def threat_life(family: str, status: str, age_min: float | None) -> float:
    """How alive a track still is: 1.0 just reported, falling to 0.0 when it's
    gone (the map fades it by this and drops it at 0).

    ``family`` is a key of LIFE_MIN, ``status`` the track's status
    (moving / circling / descending / impact / clear / launch / unknown),
    ``age_min`` minutes since its last report (None: unknown -> alive)."""
    if age_min is None:
        return 1.0
    if status in ("impact", "clear"):
        life = 5.0                           # it's over: fade out so you see it end
    else:
        life = LIFE_MIN.get(family, LIFE_MIN["unknown"])
        if status == "circling":
            life *= 1.5                      # the one case it does stay put
    hold = 0.4 * life                        # "still around, just not reported"
    if age_min <= hold:
        return 1.0
    return max(0.0, 1.0 - (age_min - hold) / (life - hold))


# A merged track gets fixes from several channels, each naming the nearest
# town it knows, posted with a few minutes' lag. Drawn in posting order, one
# channel's "Бровари" between two "Троєщина" fixes was a 20 km jump there and
# back in two minutes - not something a Shahed does. A fix is plausible when
# the object could have flown there from the neighbouring kept fix in the time
# between them: speed x elapsed, plus a floor for town-centre / posting slack.
FIX_FLOOR_KM = 12.0


def reach_kmh(slug: str, speed_kmh: dict[str, float] | None = None) -> float:
    """Fastest this type plausibly covers ground (as dedupe judges it)."""
    cruise = (speed_kmh or {}).get(slug) or SPEED_KMH.get(slug, 200.0)
    return max(cruise * 1.25, MAX_SPEED_KMH.get(slug, 0.0))


def plausible_path(pts: list[tuple[datetime, float, float]], reach: float,
                   floor_km: float = FIX_FLOOR_KM) -> list[int]:
    """Indices (ascending) of the fixes in ``pts`` - (time, lat, lon), oldest
    first - that form a physically flyable path for something no faster
    than ``reach`` km/h. The rest are outliers: kept in the feed, not drawn."""
    if len(pts) < 2:
        return list(range(len(pts)))

    def ok(a, b) -> bool:
        hours = abs((b[0] - a[0]).total_seconds()) / 3600.0
        return haversine_km((a[1], a[2]), (b[1], b[2])) <= floor_km + reach * hours

    # newest first: the latest report is where it is now, so it anchors the
    # path; an older fix stays only if it could have flown on to the next kept
    kept = [len(pts) - 1]
    for i in range(len(pts) - 2, -1, -1):
        if ok(pts[i], pts[kept[-1]]):
            kept.append(i)
    return kept[::-1]


def flyable(evs, slug: str, speed_kmh: dict[str, float] | None = None) -> list:
    """``evs`` (any order) -> the ones with a position on a flyable path,
    oldest first."""
    timed = []
    for e in evs:
        if e["lat"] is None:
            continue
        try:
            timed.append((dtp.isoparse(e["posted_at"]), e))
        except (TypeError, ValueError):
            continue
    timed.sort(key=lambda te: te[0])
    keep = plausible_path([(t, e["lat"], e["lon"]) for t, e in timed],
                          reach_kmh(slug, speed_kmh))
    return [timed[i][1] for i in keep]
