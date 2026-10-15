"""The map's period ("5 min") trims a drone track to what happened in it;
missiles keep their whole story."""

from datetime import datetime, timedelta, timezone

from dronevis.api import _cluster_dto

NOW = datetime(2026, 10, 15, 19, 50, tzinfo=timezone.utc)


def _dto(threat, since_min):
    fixes = [(-12, "Крюківщина", 50.38, 30.37), (-8, "Боярка", 50.33, 30.30),
             (-3, "Борщагівка", 50.42, 30.37), (-1, "Чайки", 50.43, 30.30)]
    evs = []
    for m, place, lat, lon in reversed(fixes):          # newest first
        iso = (NOW + timedelta(minutes=m)).isoformat()
        evs.append({"lat": lat, "lon": lon, "posted_at": iso, "r_posted": iso,
                    "altitude_m": None, "channel": f"ch{m}", "url": "u",
                    "raw_line": place, "parse_confidence": 0.9, "count": None,
                    "status": "moving", "place_name": place})
    c = {"id": 1, "threat_type": threat, "status": "moving",
         "first_posted_at": evs[-1]["posted_at"], "last_posted_at": evs[0]["posted_at"],
         "centroid_lat": 50.43, "centroid_lon": 30.30, "place_name": "Чайки",
         "dest_name": None, "dest_lat": None, "dest_lon": None, "heading_deg": None,
         "count": None, "count_max": None, "event_count": 4, "channels": "[]",
         "altitude_m": None, "resolved_at": None}
    return _cluster_dto(c, evs, now=NOW, since=NOW - timedelta(minutes=since_min))


def test_short_period_shows_only_its_part_of_a_drone_track():
    d = _dto("shahed", 5)
    assert [s["place"] for s in d["track_src"]] == ["Борщагівка", "Чайки"]
    assert [s["line"] for s in d["sources"]] == ["Чайки", "Борщагівка"]
    assert d["event_count"] == 2 and d["channels"] == ["ch-1", "ch-3"]


def test_longer_period_shows_more_of_it():
    assert len(_dto("shahed", 15)["track_src"]) == 4


def test_missiles_keep_their_whole_story():
    assert len(_dto("cruise_missile", 5)["sources"]) == 4
