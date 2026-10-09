"""Course prediction (heading / speed / spread) and danger to a point."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from dronevis.config import load_config
from dronevis.db import Database
from dronevis.geo.util import bearing_deg, dest_point, haversine_km
from dronevis.ha import compute_state
from dronevis.predict import Fix, assess, cone, dead_reckon, estimate_motion

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
KYIV = (50.4501, 30.5234)


def _ago(minutes):
    return NOW - timedelta(minutes=minutes)


def _track(start, heading, legs_km, minutes_per_leg, end_minutes_ago=0, jitter=()):
    """Fixes along a straight course, the last one ``end_minutes_ago``."""
    pts = [start]
    for km in legs_km:
        pts.append(dest_point(*pts[-1], heading, km))
    n = len(pts)
    fixes = [Fix(_ago(end_minutes_ago + (n - 1 - i) * minutes_per_leg), *p)
             for i, p in enumerate(pts)]
    for i, (dlat, dlon) in jitter:
        f = fixes[i]
        fixes[i] = Fix(f.t, f.lat + dlat, f.lon + dlon)
    return fixes


def _shahed(fixes, **kw):
    kw.setdefault("status", "moving")
    return estimate_motion(fixes, family="drone", cruise_kmh=170, **kw)


def test_heading_is_smoothed_over_recent_legs():
    # flying east; the middle fix is a town centre ~3 km off the line
    fixes = _track((50.0, 29.0), 90, [15, 15, 15], 5, jitter=[(2, (0.03, 0))])
    m = _shahed(fixes)
    assert m.heading_src == "observed"
    assert abs(m.heading_deg - 90) < 12            # the last leg alone is ~11° off
    assert m.spread_deg < 25


def test_jitter_shorter_than_a_leg_is_not_motion():
    a = (50.0, 30.0)
    fixes = [Fix(_ago(10), *a), Fix(_ago(5), *dest_point(*a, 200, 2.0))]
    m = _shahed(fixes)
    assert m.heading_src is None and not m.moving and m.horizon_min == 0


def test_observed_speed_used_and_clamped():
    # 30 km in 10 min = 180 km/h: plausible for a Shahed -> used as is
    m = _shahed(_track((50.0, 29.0), 90, [15, 15], 5))
    assert m.speed_observed and m.speed_kmh == pytest.approx(180, rel=0.02)
    # 30 km in 2x1 min would be 900 km/h - report timing, not speed -> capped
    m = _shahed(_track((50.0, 29.0), 90, [15, 15], 1.5))
    assert m.speed_kmh == pytest.approx(170 * 1.5)


def test_jet_uav_observed_dash_up_to_top_speed():
    # 45 km in 3 min = 900 km/h: a jet UAV dash, believed (not capped at 1.5x)
    fixes = _track((50.0, 29.0), 90, [22.5, 22.5], 1.5)
    m = estimate_motion(fixes, family="drone", status="moving", cruise_kmh=560,
                        max_kmh=900)
    assert m.speed_kmh == pytest.approx(900, rel=0.01)
    # nothing observed: the usual cruise speed
    m = estimate_motion(fixes[-1:], family="drone", status="moving", cruise_kmh=560,
                        max_kmh=900, reported_heading=90)
    assert m.speed_kmh == 560


def test_projection_never_reaches_further_than_max_ahead():
    slow = _shahed([Fix(NOW, 50.0, 30.0)], reported_heading=90)
    fast = estimate_motion([Fix(NOW, 50.0, 30.0)], family="drone", status="moving",
                           cruise_kmh=560, reported_heading=90)
    assert slow.horizon_min == 20                        # 57 km: within the cap
    assert fast.horizon_min == pytest.approx(6.4, abs=0.1)
    tip = cone(fast)[len(cone(fast)) // 2]
    assert haversine_km((50.0, 30.0), tip) == pytest.approx(60, abs=1)


def test_destination_beats_observed_heading():
    fixes = _track((50.0, 29.0), 90, [15, 15], 5)
    m = _shahed(fixes, dest=KYIV)
    assert m.heading_src == "dest" and m.spread_deg == 8
    assert m.heading_deg == pytest.approx(bearing_deg((fixes[-1].lat, fixes[-1].lon), KYIV))


@pytest.mark.parametrize("status", ["circling", "impact"])
def test_not_a_course(status):
    m = _shahed(_track((50.0, 29.0), 90, [15, 15], 5), status=status)
    assert not m.moving and m.horizon_min == 0 and cone(m) == []


def test_ballistic_has_no_drawn_projection_but_still_a_course():
    m = estimate_motion([Fix(_ago(1), *dest_point(*KYIV, 135, 300))], family="ballistic",
                        status="moving", cruise_kmh=2000, dest=KYIV)
    assert m.moving and m.horizon_min == 0
    d = assess(m, KYIV, NOW)
    assert d["toward"] and d["eta_min"] == pytest.approx(9, abs=0.5)
    assert d["level"] == "high"


def test_dead_reckoning():
    fixes = _track((50.0, 29.0), 90, [15, 15], 5, end_minutes_ago=6)
    m = _shahed(fixes)
    now_pos = dead_reckon(m, NOW)
    moved = haversine_km((fixes[-1].lat, fixes[-1].lon), now_pos)
    assert moved == pytest.approx(m.speed_kmh * 6 / 60, rel=0.01)
    # too old to guess where it is now
    assert dead_reckon(_shahed(_track((50.0, 29.0), 90, [15, 15], 5,
                                      end_minutes_ago=40)), NOW) is None


def test_dead_reckoning_stops_at_the_target():
    a = (50.0, 30.0)
    target = dest_point(*a, 90, 5)
    m = _shahed([Fix(_ago(15), *a)], dest=target, reported_heading=90)
    assert haversine_km(dead_reckon(m, NOW), target) < 0.1


def _west_of(ref, km):
    return dest_point(*ref, 270, km)


def test_toward_you_eta_and_level():
    for km, level in ((40, "high"), (90, "medium"), (200, "low")):
        m = _shahed([Fix(NOW, *_west_of(KYIV, km))], reported_heading=90)
        d = assess(m, KYIV, NOW)
        assert d["toward"] and d["eta_min"] == pytest.approx(km / 170 * 60, rel=0.02)
        assert d["level"] == level


def test_moving_away():
    m = _shahed([Fix(NOW, *_west_of(KYIV, 40))], reported_heading=270)
    d = assess(m, KYIV, NOW)
    assert d["receding"] and not d["toward"] and d["eta_min"] is None
    assert d["level"] == "low"


def test_passing_wide_is_not_toward():
    # 60 km west, 40 km south of the line, flying east: passes 40 km away
    p = dest_point(*_west_of(KYIV, 60), 180, 40)
    m = _shahed(_track(dest_point(*p, 270, 30), 90, [15, 15], 5))
    d = assess(m, KYIV, NOW)
    assert not d["toward"] and d["cpa_km"] == pytest.approx(40, abs=4)


def test_stopping_short_of_you_is_not_toward():
    start = _west_of(KYIV, 60)
    m = _shahed([Fix(NOW, *start)], dest=dest_point(*start, 90, 10))
    d = assess(m, KYIV, NOW)
    assert not d["toward"] and d["cpa_km"] == pytest.approx(50, abs=1)


def test_falling_altitude_is_descending_and_dangerous_nearby():
    a = _west_of(KYIV, 20)
    fixes = [Fix(_ago(6), *a, 1500), Fix(_ago(2), *a, 400)]
    m = _shahed(fixes)
    assert m.descending and m.alt_trend == "down"
    assert assess(m, KYIV, NOW)["level"] == "high"          # 20 km, coming down
    # a small wobble isn't a dive
    m = _shahed([Fix(_ago(6), *a, 500), Fix(_ago(2), *a, 450)])
    assert not m.descending and m.alt_trend == "down"
    assert assess(m, KYIV, NOW)["level"] == "medium"         # near, direction unknown


def test_old_or_finished_tracks_are_not_a_danger():
    near = _west_of(KYIV, 5)
    old = _shahed([Fix(_ago(120), *near)], reported_heading=90)
    d = assess(old, KYIV, NOW, max_age_min=30)
    assert d["stale"] and d["level"] == "low" and not d["toward"]
    assert assess(old, KYIV, NOW)["level"] == "high"          # no age limit given
    hit = _shahed([Fix(_ago(2), *near)], status="impact")
    assert assess(hit, KYIV, NOW, max_age_min=30)["level"] == "low"


def test_unknown_direction_level_scales_with_speed():
    shahed = _shahed([Fix(NOW, *_west_of(KYIV, 100))])
    assert assess(shahed, KYIV, NOW)["level"] == "low"
    ballistic = estimate_motion([Fix(NOW, *_west_of(KYIV, 300))], family="ballistic",
                                status="unknown", cruise_kmh=2000)
    assert assess(ballistic, KYIV, NOW)["level"] == "medium"
    near = _shahed([Fix(NOW, *_west_of(KYIV, 6))])
    assert assess(near, KYIV, NOW)["level"] == "high"


# ------------------------------------------------------------------ HA state
@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    yield d
    d.close()


def _cluster(db, lat, lon, **kw):
    now = datetime.now(timezone.utc).isoformat()
    row = dict(threat_type="jet_uav", status="moving", first_posted_at=now,
               last_posted_at=now, centroid_lat=lat, centroid_lon=lon,
               place_name="P", heading_deg=None, count=1, count_max=1,
               event_count=1, channels=json.dumps(["kpszsu"]))
    row.update(kw)
    cid = db.insert_cluster(row)
    rid, _ = db.upsert_raw_message("kpszsu", cid, "u", now, "t")
    eid = db.insert_event({"raw_message_id": rid, "channel": "kpszsu", "posted_at": now,
                           "threat_type": row["threat_type"], "status": "moving",
                           "lat": lat, "lon": lon, "geo_confidence": 0.9,
                           "parse_confidence": 0.9, "parse_method": "rules"})
    db.set_event_cluster(eid, cid)
    return cid


def test_ha_counts_a_track_about_to_enter_the_area(db):
    cfg = load_config()
    cfg.alarm.predict_minutes = 20
    radius = cfg.default_area.radius_km
    outside = dest_point(*cfg.default_area.center, 90, radius + 10)
    _cluster(db, *outside, heading_deg=270)              # flying straight in
    s = compute_state(db, cfg)
    assert s["threats"]["jet_uav"]["on"]
    assert s["danger"]["toward"] == 1 and s["danger"]["eta_min"] < 20
    assert s["danger"]["level"] in ("medium", "high")

    cfg.alarm.predict_minutes = 0                         # early warning off
    assert "jet_uav" not in compute_state(db, cfg)["threats"]


def test_ha_ignores_a_track_outside_flying_away(db):
    cfg = load_config()
    radius = cfg.default_area.radius_km
    outside = dest_point(*cfg.default_area.center, 90, radius + 10)
    _cluster(db, *outside, heading_deg=90)
    s = compute_state(db, cfg)
    assert not s["threats"] and s["danger"]["level"] == "none"
