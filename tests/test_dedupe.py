"""Cross-channel clustering."""

from datetime import datetime, timedelta, timezone

import pytest

from dronevis.config import load_config
from dronevis.db import Database
from dronevis.dedupe import Deduper
from dronevis.parse.pipeline import ParsedEvent


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    yield d
    d.close()


@pytest.fixture
def deduper(db):
    return Deduper(db, load_config())


def _raw(db, channel, mid, when):
    rid, _ = db.upsert_raw_message(channel, mid, f"http://x/{mid}", when, "txt")
    return rid


def _ev(**kw):
    base = dict(
        threat_type="shahed", threat_raw="шахед", count=1, status="moving",
        place_name="Бровари", lat=50.51, lon=30.79, geo_confidence=0.9,
        raw_line="шахед над броварами", parse_confidence=0.9,
    )
    base.update(kw)
    return ParsedEvent(**base)


def _store(db, deduper, rid, ev, channel, when):
    row = ev.to_row()
    row.update(raw_message_id=rid, channel=channel, posted_at=when)
    eid = db.insert_event(row)
    return deduper.assign(eid, ev, channel, when)


def test_two_channels_same_object_merge(db, deduper):
    t0 = datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=4)
    c1 = _store(db, deduper, _raw(db, "kpszsu", 1, t0.isoformat()),
                _ev(), "kpszsu", t0.isoformat())
    c2 = _store(db, deduper, _raw(db, "war_monitor", 2, t1.isoformat()),
                _ev(place_name="Бровари", lat=50.52, lon=30.80),
                "war_monitor", t1.isoformat())
    assert c1 == c2
    row = db.query_one("SELECT * FROM cluster WHERE id=?", (c1,))
    assert row["event_count"] == 2
    assert set(__import__("json").loads(row["channels"])) == {"kpszsu", "war_monitor"}


def test_far_apart_do_not_merge(db, deduper):
    t0 = datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc)
    c1 = _store(db, deduper, _raw(db, "a", 1, t0.isoformat()),
                _ev(place_name="Бровари", lat=50.51, lon=30.79), "a", t0.isoformat())
    c2 = _store(db, deduper, _raw(db, "b", 2, (t0 + timedelta(minutes=2)).isoformat()),
                _ev(place_name="Одеса", lat=46.48, lon=30.72), "b",
                (t0 + timedelta(minutes=2)).isoformat())
    assert c1 != c2


def test_different_family_never_merges(db, deduper):
    t0 = datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc)
    c1 = _store(db, deduper, _raw(db, "a", 1, t0.isoformat()),
                _ev(threat_type="shahed"), "a", t0.isoformat())
    c2 = _store(db, deduper, _raw(db, "b", 2, (t0 + timedelta(minutes=1)).isoformat()),
                _ev(threat_type="cruise_missile"), "b",
                (t0 + timedelta(minutes=1)).isoformat())
    assert c1 != c2


def test_time_gap_splits(db, deduper):
    t0 = datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc)
    late = t0 + timedelta(hours=3)
    c1 = _store(db, deduper, _raw(db, "a", 1, t0.isoformat()), _ev(), "a", t0.isoformat())
    c2 = _store(db, deduper, _raw(db, "a", 2, late.isoformat()), _ev(), "a", late.isoformat())
    assert c1 != c2


def test_unknown_only_merges_on_exact_place(db, deduper):
    t0 = datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc)
    c1 = _store(db, deduper, _raw(db, "a", 1, t0.isoformat()),
                _ev(threat_type="unknown", place_name="Ірпінь", lat=50.52, lon=30.25),
                "a", t0.isoformat())
    # nearby but different name -> stays separate
    c2 = _store(db, deduper, _raw(db, "b", 2, (t0 + timedelta(minutes=3)).isoformat()),
                _ev(threat_type="unknown", place_name="Буча", lat=50.54, lon=30.21),
                "b", (t0 + timedelta(minutes=3)).isoformat())
    assert c1 != c2


# Slavutych 51.52,30.76  ->  Dymer 50.79,30.30  is ~90 km, roughly south.
SLAVUTYCH = (51.5225, 30.7566)
DYMER = (50.79, 30.30)


def test_trajectory_chains_same_group_one_channel(db, deduper):
    t0 = datetime(2026, 8, 30, 22, 0, tzinfo=timezone.utc)
    c1 = _store(db, deduper, _raw(db, "war_monitor", 1, t0.isoformat()),
                _ev(threat_type="jet_uav", count=5, place_name="Славутич",
                    lat=SLAVUTYCH[0], lon=SLAVUTYCH[1], heading_deg=200),
                "war_monitor", t0.isoformat())
    t1 = t0 + timedelta(minutes=9)                 # ~90 km is reachable for a jet UAV
    c2 = _store(db, deduper, _raw(db, "war_monitor", 2, t1.isoformat()),
                _ev(threat_type="jet_uav", count=5, place_name="Димер",
                    lat=DYMER[0], lon=DYMER[1]),
                "war_monitor", t1.isoformat())
    assert c1 == c2
    row = db.query_one("SELECT * FROM cluster WHERE id=?", (c1,))
    assert row["count"] == 5 and row["count_max"] == 5
    assert (row["centroid_lat"], row["centroid_lon"]) == DYMER   # marker moved


def test_trajectory_needs_matching_size(db, deduper):
    t0 = datetime(2026, 8, 30, 22, 0, tzinfo=timezone.utc)
    c1 = _store(db, deduper, _raw(db, "war_monitor", 1, t0.isoformat()),
                _ev(threat_type="jet_uav", count=5, place_name="Славутич",
                    lat=SLAVUTYCH[0], lon=SLAVUTYCH[1], heading_deg=200),
                "war_monitor", t0.isoformat())
    t1 = t0 + timedelta(minutes=9)
    c2 = _store(db, deduper, _raw(db, "war_monitor", 2, t1.isoformat()),
                _ev(threat_type="jet_uav", count=1, place_name="Димер",
                    lat=DYMER[0], lon=DYMER[1]),
                "war_monitor", t1.isoformat())
    assert c1 != c2                                  # 5 vs 1 -> different groups


def test_trajectory_rejects_upstream_jump(db, deduper):
    t0 = datetime(2026, 8, 30, 22, 0, tzinfo=timezone.utc)
    c1 = _store(db, deduper, _raw(db, "war_monitor", 1, t0.isoformat()),
                _ev(threat_type="jet_uav", count=5, place_name="Димер",
                    lat=DYMER[0], lon=DYMER[1], heading_deg=200),  # heading south
                "war_monitor", t0.isoformat())
    t1 = t0 + timedelta(minutes=9)
    # next sighting is back north at Slavutych - opposite the heading
    c2 = _store(db, deduper, _raw(db, "war_monitor", 2, t1.isoformat()),
                _ev(threat_type="jet_uav", count=5, place_name="Славутич",
                    lat=SLAVUTYCH[0], lon=SLAVUTYCH[1]),
                "war_monitor", t1.isoformat())
    assert c1 != c2


def test_trajectory_rejects_teleport(db, deduper):
    t0 = datetime(2026, 8, 30, 22, 0, tzinfo=timezone.utc)
    c1 = _store(db, deduper, _raw(db, "war_monitor", 1, t0.isoformat()),
                _ev(threat_type="shahed", count=3, place_name="Славутич",
                    lat=SLAVUTYCH[0], lon=SLAVUTYCH[1], heading_deg=180),
                "war_monitor", t0.isoformat())
    t1 = t0 + timedelta(minutes=3)                 # a Shahed can't cross ~90 km in 3 min
    c2 = _store(db, deduper, _raw(db, "war_monitor", 2, t1.isoformat()),
                _ev(threat_type="shahed", count=3, place_name="Київ",
                    lat=50.45, lon=30.52),
                "war_monitor", t1.isoformat())
    assert c1 != c2


# Chernihiv 51.49,31.29  ->  Poltava 49.59,34.55  is ~325 km.
CHERNIHIV = (51.4982, 31.2893)
POLTAVA = (49.5883, 34.5514)


def test_trajectory_rejects_cross_country_hop_even_for_banderol(db, deduper):
    t0 = datetime(2026, 8, 30, 22, 0, tzinfo=timezone.utc)
    c1 = _store(db, deduper, _raw(db, "war_monitor", 1, t0.isoformat()),
                _ev(threat_type="cruise_missile", count=2, place_name="Чернігів",
                    lat=CHERNIHIV[0], lon=CHERNIHIV[1], heading_deg=127),
                "war_monitor", t0.isoformat())
    t1 = t0 + timedelta(minutes=18)               # ~325 km would need >1000 km/h
    c2 = _store(db, deduper, _raw(db, "war_monitor", 2, t1.isoformat()),
                _ev(threat_type="cruise_missile", count=2, place_name="Полтава",
                    lat=POLTAVA[0], lon=POLTAVA[1]),
                "war_monitor", t1.isoformat())
    assert c1 != c2


def test_trajectory_absolute_hop_cap(db, deduper):
    """Even with a generous time gap, a >max_hop_km single jump never chains."""
    t0 = datetime(2026, 8, 30, 22, 0, tzinfo=timezone.utc)
    c1 = _store(db, deduper, _raw(db, "war_monitor", 1, t0.isoformat()),
                _ev(threat_type="jet_uav", count=4, place_name="Чернігів",
                    lat=CHERNIHIV[0], lon=CHERNIHIV[1], heading_deg=135),
                "war_monitor", t0.isoformat())
    t1 = t0 + timedelta(minutes=24)               # 24 min * 560 km/h would "allow" ~280 km
    c2 = _store(db, deduper, _raw(db, "war_monitor", 2, t1.isoformat()),
                _ev(threat_type="jet_uav", count=4, place_name="Полтава",
                    lat=POLTAVA[0], lon=POLTAVA[1]),
                "war_monitor", t1.isoformat())
    assert c1 != c2


def test_count_and_peak_tracked(db, deduper):
    t0 = datetime(2026, 8, 30, 22, 0, tzinfo=timezone.utc)
    cid = _store(db, deduper, _raw(db, "a", 1, t0.isoformat()),
                 _ev(count=5, place_name="Снігурівка", lat=47.07, lon=32.81), "a",
                 t0.isoformat())
    _store(db, deduper, _raw(db, "a", 2, (t0 + timedelta(minutes=6)).isoformat()),
           _ev(count=2, place_name="Снігурівка", lat=47.07, lon=32.81), "a",
           (t0 + timedelta(minutes=6)).isoformat())
    row = db.query_one("SELECT count, count_max FROM cluster WHERE id=?", (cid,))
    assert row["count"] == 2 and row["count_max"] == 5
