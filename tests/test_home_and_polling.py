"""Home from Home Assistant as the danger point; adaptive polling."""

import asyncio
from datetime import datetime, timedelta, timezone

from dronevis.config import _latlon_pair, load_config
from dronevis.predict import _level


def test_home_parsing(monkeypatch, tmp_path):
    assert _latlon_pair("50.45,30.52") == (50.45, 30.52)
    assert _latlon_pair({"lat": 50.45, "lon": 30.52}) == (50.45, 30.52)
    assert _latlon_pair("null,null") is None and _latlon_pair("0,0") is None
    monkeypatch.setenv("DRONEVIS_DB_PATH", str(tmp_path / "h.db"))
    monkeypatch.setenv("DRONEVIS_HOME", "50.40,30.62")
    assert load_config().home == (50.40, 30.62)


def test_close_to_home_is_at_least_medium():
    assert _level(25, False, None, True, 30) == "medium"                    # 25 km, sideways
    assert _level(25, False, None, True, 30, receding=True) == "low"        # leaving
    assert _level(60, False, None, True, 30) == "low"
    assert _level(8, False, None, True, 3) == "high"


def test_api_measures_from_home(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from dronevis.api import create_app

    monkeypatch.setenv("DRONEVIS_DB_PATH", str(tmp_path / "a.db"))
    monkeypatch.setenv("DRONEVIS_HOME", "50.40,30.62")
    c = TestClient(create_app(load_config()))
    assert c.get("/api/config").json()["home"] == [50.40, 30.62]
    ref = c.get("/api/clusters?area=kyiv").json()["ref"]
    assert ref["kind"] == "home" and (ref["lat"], ref["lon"]) == (50.40, 30.62)
    # the viewer's own point still wins
    ref = c.get("/api/clusters?area=kyiv&ref=50.5,30.5").json()["ref"]
    assert ref["kind"] == "here"


def test_threat_active_drives_fast_polling(monkeypatch, tmp_path):
    from dronevis.service import Service

    monkeypatch.setenv("DRONEVIS_DB_PATH", str(tmp_path / "p.db"))
    svc = Service(load_config())
    try:
        now = datetime.now(timezone.utc)
        assert not svc.threat_active(now)                     # quiet: slow polling
        text = "🛵 БпЛА на Бровари з півночі"
        rid, _ = svc.db.upsert_raw_message("kpszsu", 1, "u", (now - timedelta(minutes=5)).isoformat(), text)
        svc._parse_and_store(rid, "kpszsu", text, (now - timedelta(minutes=5)).isoformat())
        assert svc.threat_active(now)                         # a fresh report: fast
        assert not svc.threat_active(now + timedelta(minutes=30))
        av = "✈️ Ту-95МС в повітрі"
        svc.db.upsert_raw_message("kpszsu", 2, "u2", now.isoformat(), av)
        assert svc.threat_active(now + timedelta(minutes=30))  # bombers up: still fast
    finally:
        asyncio.run(svc.aclose())
