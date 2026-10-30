"""Ballistic / missile threats: launch areas, one missile per track, the
trajectory to the target. Sequences are real posts from 2026-10-10."""

from datetime import datetime, timedelta, timezone

import pytest

from dronevis.config import load_config
from dronevis.db import Database
from dronevis.dedupe import Deduper
from dronevis.geo.launch import candidates, reaches, site_at
from dronevis.parse.pipeline import Parser

KYIV = (50.45, 30.52)
T0 = datetime(2026, 10, 10, 21, 31, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def parser():
    return Parser(load_config())


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "m.db")
    yield d
    d.close()


def test_parse_words(parser):
    (e,) = parser.parse("Курськ ОТРК!", channel="AerisRimor")
    assert e.threat_type == "iskander" and e.place_name == "Курськ"
    (s,) = parser.parse("‼️ Київ — спуск балістики!", channel="war_monitor")
    assert s.status == "descending"
    (z,) = parser.parse("❗️ 1х Циркон повз Ніжин, далі Київ", channel="war_monitor")
    assert z.place_name == "Ніжин" and z.dest_name == "Київ"
    (v,) = parser.parse("❗️ Вихід Циркону Курьск", channel="war_monitor")
    assert v.status == "launch" and site_at(v.lat, v.lon).name == "Курськ"


def test_launch_areas_and_their_usual_targets():
    names = lambda pt: [s.name for s in candidates(pt)]           # noqa: E731
    assert names(KYIV) == ["Брянськ", "Курськ", "Бєлгород"]
    assert names((46.48, 30.72)) == ["Крим"]                      # Одеса
    kursk, crimea, voronezh = (site_at(51.73, 36.19), site_at(45.71, 34.39),
                               site_at(51.67, 39.18))
    assert reaches(kursk, KYIV) and not reaches(crimea, KYIV) and not reaches(voronezh, KYIV)
    assert site_at(49.99, 36.23) is None                          # Харків isn't Бєлгород


def _store(db, dd, parser, channel, mid, text, when):
    rid, _ = db.upsert_raw_message(channel, mid, f"u/{channel}/{mid}", when.isoformat(), text)
    out = []
    for ev in parser.parse(text, channel=channel):
        row = ev.to_row()
        row.update(raw_message_id=rid, channel=channel, posted_at=when.isoformat())
        out.append(dd.assign(db.insert_event(row), ev, channel, when.isoformat()))
    return out


def test_one_threat_from_three_channels(db, parser):
    dd = Deduper(db, load_config())
    a = _store(db, dd, parser, "kievreal1", 1, "Загроза балістики з Курська", T0)
    b = _store(db, dd, parser, "kpszsu", 2,
               "☄️ Загроза застосування балістичного озброєння з Курської області!",
               T0 + timedelta(minutes=1))
    c = _store(db, dd, parser, "AerisRimor", 3, "Курськ ОТРК!", T0 + timedelta(minutes=2))
    assert a == b == c


def test_missile_sequence(db, parser):
    """war_monitor 10:58-11:09: two Zircons to Kyiv; a ballistic descent; a
    threat that becomes the third descent and hits; then a NEW Zircon launch
    from Kursk that flies past Nizhyn toward Kyiv."""
    dd = Deduper(db, load_config())
    t = datetime(2026, 10, 10, 10, 58, tzinfo=timezone.utc)
    m = lambda mins: t + timedelta(minutes=mins)                   # noqa: E731
    w = "war_monitor"
    (z1,) = _store(db, dd, parser, w, 37, "❗️2х Циркони вектор повз Ніжин.", m(0))
    (z1b,) = _store(db, dd, parser, w, 38, "❗️ 2х Циркони далі Київ", m(1))
    assert z1b == z1                                   # the channel updates its missile
    (b1,) = _store(db, dd, parser, w, 39, "‼️ Київ — спуск балістики!", m(1.5))
    (b2,) = _store(db, dd, parser, w, 42, "💥 Вибухи у Києві\nЗагроза балістики триває.", m(2.5))
    assert b2 != b1                                    # after спуск: another missile
    (z2,) = _store(db, dd, parser, w, 43, "❗️ Вихід Циркону Курьск", m(6))
    assert z2 != z1                                    # a new launch, not z1 turning back
    (z2b,) = _store(db, dd, parser, w, 46, "❗️ 1х Циркон повз Ніжин, далі Київ", m(9))
    assert z2b == z2
    (b3,) = _store(db, dd, parser, w, 47, "‼️ Київ — спуск балістики! Третя.", m(10))
    assert b3 == b2                                    # the ongoing threat descends
    (b4,) = _store(db, dd, parser, w, 51, "💥 Вибухи в Києві. Загроза балістики триває.", m(11.5))
    assert b4 == b2                                    # ... and that missile hit


def test_api_trajectory_and_area(db, parser):
    from dronevis.api import _launch_aims_at, _missile_info

    dd = Deduper(db, load_config())
    (cid,) = _store(db, dd, parser, "kievreal1", 1, "Загроза балістики з Курська", T0)
    c = db.query_one("SELECT * FROM cluster WHERE id=?", (cid,))
    evs = db.query("SELECT e.*, r.url FROM event e JOIN raw_message r ON r.id=e.raw_message_id "
                   "WHERE e.cluster_id=? ORDER BY e.posted_at DESC", (cid,))
    assert _launch_aims_at(c, KYIV)                     # shows on a Kyiv map
    assert not _launch_aims_at(c, (46.48, 30.72))       # but not on Odesa's
    m = _missile_info(c, evs, KYIV)
    assert m["launch"]["name"] == "Курськ" and m["to_kind"] == "area"
    assert 400 <= m["km"] <= 440 and 3 <= m["min"] <= 6 and not m["final"]


def test_reply_all_clear_ends_the_threat(tmp_path, monkeypatch):
    import asyncio

    from dronevis.service import Service

    monkeypatch.setenv("DRONEVIS_DB_PATH", str(tmp_path / "s.db"))
    svc = Service(load_config())
    try:
        rid, _ = svc.db.upsert_raw_message("kpszsu", 85010, "u", T0.isoformat(),
                                           "☄️ Загроза застосування балістичного озброєння з Курської області!")
        svc._parse_and_store(rid, "kpszsu", "☄️ Загроза застосування балістичного озброєння з Курської області!",
                             T0.isoformat())
        later = (T0 + timedelta(minutes=19)).isoformat()
        rid2, _ = svc.db.upsert_raw_message("kpszsu", 85013, "u2", later,
                                            "📢 Відбій загрози застосування балістичного озброєння.", 85010)
        svc._parse_and_store(rid2, "kpszsu", "📢 Відбій загрози застосування балістичного озброєння.",
                             later, reply_to=85010)
        open_ = svc.db.query_one("SELECT COUNT(*) n FROM cluster WHERE resolved_at IS NULL")["n"]
        assert open_ == 0
    finally:
        asyncio.run(svc.aclose())


def test_quiet_threat_is_a_possible_false_alarm(db, parser):
    from dronevis.api import _missile_info

    dd = Deduper(db, load_config())
    (cid,) = _store(db, dd, parser, "AerisRimor", 1, "Курськ ОТРК!", T0)
    c = db.query_one("SELECT * FROM cluster WHERE id=?", (cid,))
    evs = db.query("SELECT e.*, r.url FROM event e JOIN raw_message r ON r.id=e.raw_message_id "
                   "WHERE e.cluster_id=?", (cid,))
    assert not _missile_info(c, evs, KYIV, T0 + timedelta(minutes=5))["maybe_false"]
    assert _missile_info(c, evs, KYIV, T0 + timedelta(minutes=11))["maybe_false"]


def test_missile_threat_outlives_a_short_window(tmp_path, monkeypatch):
    """A 5-minute map still shows a 'Загроза ОТРК, Курськ' posted 6 min ago
    (unresolved), but not after its hold time or once resolved."""
    from fastapi.testclient import TestClient

    from dronevis.api import create_app

    monkeypatch.setenv("DRONEVIS_DB_PATH", str(tmp_path / "w.db"))
    cfg = load_config()
    app = create_app(cfg)
    client = TestClient(app)
    db = Database(cfg.database_path)
    dd = Deduper(db, cfg)
    p = Parser(cfg)
    now = datetime.now(timezone.utc)
    (cid,) = _store(db, dd, p, "AerisRimor", 1, "Курськ ОТРК!", now - timedelta(minutes=6))
    (old,) = _store(db, dd, p, "AerisRimor", 2, "Брянськ ОТРК!", now - timedelta(minutes=10))
    ids = {c["id"] for c in client.get("/api/clusters?area=kyiv&since=-5m").json()["clusters"]}
    assert cid in ids and old not in ids                  # held 7 min for ballistic
    db.update_cluster(cid, {"resolved_at": now.isoformat()})
    ids = {c["id"] for c in client.get("/api/clusters?area=kyiv&since=-5m").json()["clusters"]}
    assert cid not in ids
    db.close()
