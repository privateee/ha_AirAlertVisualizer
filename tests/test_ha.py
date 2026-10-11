"""HA state computation + alarm slug expansion."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from dronevis.config import load_config
from dronevis.db import Database
from dronevis.ha import HAPublisher, compute_state, expand_alarm_slugs


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


def test_compute_state_includes_per_cluster_detail(db):
    cid = _cluster(db, threat_type="shahed", place_name="Бровари", count=3)
    s = compute_state(db, load_config())
    detail = s["threats"]["shahed"]["cluster_detail"]
    assert [d["id"] for d in detail] == [cid]
    assert detail[0]["place_name"] == "Бровари" and detail[0]["count"] == 3


# ---- event.dronevis_detected -------------------------------------------
class _FakeClient:
    def __init__(self):
        self.calls: list[tuple[str, str, bool]] = []

    def publish(self, topic, payload, retain=False):
        self.calls.append((topic, payload, retain))


def _publisher() -> tuple[HAPublisher, _FakeClient]:
    pub = HAPublisher(load_config())
    client = _FakeClient()
    pub._client = client
    pub._connected = True
    return pub, client


def _events(client: _FakeClient) -> list[dict]:
    return [json.loads(p) for t, p, r in client.calls if t.endswith("/event/detected")]


def test_first_publish_primes_silently_no_events(db):
    cid = _cluster(db, threat_type="shahed")
    pub, client = _publisher()
    pub.publish(compute_state(db, load_config()))
    assert _events(client) == []
    assert pub._seen_cluster_ids == {cid}


def test_new_cluster_after_priming_fires_one_event(db):
    pub, client = _publisher()
    pub.publish(compute_state(db, load_config()))     # prime on an empty DB
    cid = _cluster(db, threat_type="shahed", place_name="Бровари", count=2)
    pub.publish(compute_state(db, load_config()))
    evs = _events(client)
    assert len(evs) == 1
    assert evs[0]["event_type"] == "shahed"
    assert evs[0]["place_name"] == "Бровари"
    assert evs[0]["count"] == 2


def test_same_cluster_does_not_refire_on_next_publish(db):
    pub, client = _publisher()
    pub.publish(compute_state(db, load_config()))
    _cluster(db, threat_type="shahed")
    pub.publish(compute_state(db, load_config()))
    assert len(_events(client)) == 1
    client.calls.clear()
    pub.publish(compute_state(db, load_config()))      # same cluster, unchanged
    assert _events(client) == []


def test_a_second_new_cluster_fires_again_while_first_still_active(db):
    """This is the whole point of the event entity: binary_sensor.dronevis_alarm
    stays "on" across both, so only the event entity tells you a *second*
    report just came in."""
    pub, client = _publisher()
    pub.publish(compute_state(db, load_config()))
    _cluster(db, threat_type="shahed", place_name="A")
    pub.publish(compute_state(db, load_config()))
    _cluster(db, threat_type="shahed", place_name="B")
    pub.publish(compute_state(db, load_config()))
    evs = _events(client)
    assert [e["place_name"] for e in evs] == ["A", "B"]


def _state(*tracks):
    """A hand-made compute_state() result: (id, level, toward, eta_min)."""
    detail = [{"id": i, "place_name": f"P{i}", "count": 1, "dest_name": None,
               "channels": ["kpszsu"], "nearest_km": 20.0, "nearest_bearing": "N",
               "confidence": 0.9, "updated": "", "level": lvl, "toward": tw,
               "eta_min": eta, "passes_km": None, "message": "m", "line": "l",
               "url": f"https://t.me/kpszsu/{i}", "post_channel": "kpszsu", "summary": "s"}
              for i, lvl, tw, eta in tracks]
    return {"threats": {"shahed": {"label": "Shahed", "cluster_detail": detail}},
            "area": "Kyiv"}


def _fire(pub, state):
    client = _FakeClient()
    pub._publish_new_detections("dv", client.publish, state)
    return [(e["cluster_id"], e["reason"]) for e in _events(client)]


def test_known_track_refires_once_when_it_gets_more_dangerous():
    pub, _ = _publisher()
    assert _fire(pub, _state((1, "low", False, None))) == []          # prime
    assert _fire(pub, _state((1, "low", False, None))) == []
    assert _fire(pub, _state((1, "medium", True, 30))) == [(1, "escalated")]
    assert _fire(pub, _state((1, "medium", True, 25))) == []          # same level
    assert _fire(pub, _state((1, "low", False, None))) == []          # eased off...
    assert _fire(pub, _state((1, "medium", True, 30))) == []          # ...back: no repeat
    assert _fire(pub, _state((1, "high", True, 8))) == [(1, "escalated")]


def test_turning_toward_you_fires_once_and_new_beats_escalated():
    pub, _ = _publisher()
    _fire(pub, _state())
    assert _fire(pub, _state((1, "high", True, 5))) == [(1, "new")]   # one event, not two
    assert _fire(pub, _state((1, "high", True, 5), (2, "low", False, None))) == [(2, "new")]
    assert _fire(pub, _state((1, "high", True, 5), (2, "low", True, 50))) == [(2, "toward")]
    assert _fire(pub, _state((1, "high", True, 5), (2, "low", True, 40))) == []
    # far-off "toward" (arrives in 3 h) isn't news
    assert _fire(pub, _state((1, "high", True, 5), (2, "low", True, 40),
                             (3, "low", False, None))) == [(3, "new")]
    assert _fire(pub, _state((3, "low", True, 180))) == []


def test_event_carries_the_post_text_link_and_summary(db):
    pub, client = _publisher()
    pub.publish(compute_state(db, load_config()))
    _cluster(db, threat_type="shahed", place_name="Бровари", count=2)
    pub.publish(compute_state(db, load_config()))
    ev = _events(client)[0]
    assert ev["reason"] == "new" and ev["url"] == "u" and ev["message"] == "t"
    assert "×2 · Бровари · " in ev["summary"] and " km " in ev["summary"]


def test_whole_oblast_fix_is_not_high_danger_by_being_near(db):
    """"Київська обл." is placed at the oblast's centre, on top of Kyiv - that
    alone mustn't fire a "high danger" alert."""
    cfg = load_config()
    _cluster(db, threat_type="jet_uav", place_name="Київська обл.",
             centroid_lat=50.45, centroid_lon=30.52)
    _cluster(db, threat_type="ballistic", place_name="Київська обл.",
             centroid_lat=50.45, centroid_lon=30.52)
    _cluster(db, threat_type="shahed", place_name="Київ", centroid_lat=50.45, centroid_lon=30.52)
    s = compute_state(db, cfg)
    lvl = {k: t["cluster_detail"][0]["level"] for k, t in s["threats"].items()}
    assert lvl["jet_uav"] == "low" and lvl["ballistic"] == "medium"
    assert lvl["shahed"] == "high"                    # a real place stays as it is
