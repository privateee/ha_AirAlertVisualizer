"""HA state computation + alarm slug expansion."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from dronevis.config import load_config
from dronevis.db import Database
from dronevis.ha import compute_state, expand_alarm_slugs


def test_expand_alarm_slugs_family_and_slug():
    # "ballistic" is a family -> expands
    assert expand_alarm_slugs(["ballistic"]) == {"ballistic", "iskander", "kinzhal"}
    # a bare slug stays itself
    assert expand_alarm_slugs(["banderol"]) == {"banderol"}
    assert expand_alarm_slugs(["cruise"]) == {
        "banderol", "kalibr", "x101", "x22", "cruise_missile"
    }
    assert expand_alarm_slugs(["nonsense"]) == set()


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    yield d
    d.close()


def _cluster(db, **kw):
    now = datetime.now(timezone.utc).isoformat()
    base = dict(
        threat_type="shahed", status="moving", first_posted_at=now, last_posted_at=now,
        centroid_lat=50.0, centroid_lon=30.4, place_name="Test",
        dest_name=None, dest_lat=None, dest_lon=None, heading_deg=0,
        count=1, count_max=1, event_count=1, channels=json.dumps(["kpszsu"]),
    )
    base.update(kw)
    cid = db.insert_cluster(base)
    rid, _ = db.upsert_raw_message("x", cid, "u", now, "t")
    db.insert_event({
        "raw_message_id": rid, "channel": "kpszsu", "posted_at": now,
        "threat_type": base["threat_type"], "status": "moving", "place_name": "x",
        "lat": base["centroid_lat"], "lon": base["centroid_lon"],
        "geo_confidence": 0.9, "parse_confidence": kw.get("_conf", 0.9),
        "raw_line": "line", "parse_method": "rules",
    })
    db.execute("UPDATE event SET cluster_id=? WHERE raw_message_id=?", (cid, rid))
    return cid


def test_compute_state_alarm_only_for_configured(db):
    cfg = load_config()  # alarm.threats default = ["ballistic"]
    _cluster(db, threat_type="shahed", centroid_lat=50.3, centroid_lon=30.4)
    _cluster(db, threat_type="ballistic", centroid_lat=49.9, centroid_lon=31.2,
             dest_name="Київ", dest_lat=50.45, dest_lon=30.52)

    s = compute_state(db, cfg)
    assert s["threats"]["shahed"]["on"] and s["threats"]["ballistic"]["on"]
    assert s["danger"]["on"] is True
    assert s["alarm"]["on"] is True                      # ballistic is listed
    assert s["alarm"]["types"] == ["ballistic"]
    assert "Ballistic" in s["alarm"]["message"]
    assert s["threats"]["ballistic"]["heading_to"] == ["Київ"]


def test_alarm_off_when_only_non_listed_threats(db):
    cfg = load_config()
    _cluster(db, threat_type="shahed")
    _cluster(db, threat_type="banderol")
    s = compute_state(db, cfg)
    assert s["danger"]["on"] is True
    assert s["alarm"]["on"] is False


def test_resolved_and_stale_clusters_excluded(db):
    cfg = load_config()
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    _cluster(db, threat_type="ballistic", last_posted_at=old, first_posted_at=old)
    cid = _cluster(db, threat_type="ballistic")
    db.update_cluster(cid, {"resolved_at": datetime.now(timezone.utc).isoformat()})
    s = compute_state(db, cfg)
    assert s["alarm"]["on"] is False and s["active"] == 0
