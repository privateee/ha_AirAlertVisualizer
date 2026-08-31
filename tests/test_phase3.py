"""Phase 3 - retention pruning and incremental reparse (DB + Service level)."""

from datetime import datetime, timedelta, timezone

import pytest

from dronevis.config import load_config
from dronevis.db import Database
from dronevis.dedupe import Deduper
from dronevis.parse.pipeline import ParsedEvent
from dronevis.service import Service

NOW = datetime.now(timezone.utc)


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    yield d
    d.close()


@pytest.fixture
def deduper(db):
    return Deduper(db, load_config())


def _ev(**kw):
    base = dict(
        threat_type="shahed", threat_raw="шахед", count=1, status="moving",
        place_name="Бровари", lat=50.51, lon=30.79, geo_confidence=0.9,
        raw_line="шахед над броварами", parse_method="rules", parse_confidence=0.9,
    )
    base.update(kw)
    return ParsedEvent(**base)


def _store(db, deduper, channel, mid, when, ev):
    rid, _ = db.upsert_raw_message(channel, mid, f"http://x/{mid}", when, "txt")
    db.mark_parsed(rid)
    row = ev.to_row()
    row.update(raw_message_id=rid, channel=channel, posted_at=when)
    eid = db.insert_event(row)
    cid = deduper.assign(eid, ev, channel, when)
    return rid, cid


# ---------------------------------------------------------------- prune
def test_prune_drops_old_posts_and_cascades_events(db, deduper):
    old = (NOW - timedelta(days=30)).isoformat()
    fresh = (NOW - timedelta(hours=2)).isoformat()
    _store(db, deduper, "a", 1, old, _ev())
    _store(db, deduper, "a", 2, fresh, _ev(place_name="Ірпінь", lat=50.52, lon=30.25))

    removed = db.prune((NOW - timedelta(days=14)).isoformat())

    assert removed["raw_messages"] == 1
    assert db.query_one("SELECT COUNT(*) n FROM raw_message")["n"] == 1
    # the old post's event is gone via ON DELETE CASCADE
    assert db.query_one("SELECT COUNT(*) n FROM event")["n"] == 1


def test_prune_drops_stale_clusters(db, deduper):
    old = (NOW - timedelta(days=20)).isoformat()
    _store(db, deduper, "a", 1, old, _ev())
    db.prune((NOW - timedelta(days=14)).isoformat())
    assert db.query_one("SELECT COUNT(*) n FROM cluster")["n"] == 0


# ------------------------------------------------ reset_derived_since
def test_reset_since_leaves_old_untouched(db, deduper):
    old = (NOW - timedelta(hours=30)).isoformat()
    recent = (NOW - timedelta(hours=2)).isoformat()
    _store(db, deduper, "a", 1, old, _ev(place_name="Суми", lat=50.91, lon=34.80))
    _store(db, deduper, "a", 2, recent, _ev(place_name="Ромни", lat=50.75, lon=33.47))

    dropped = db.reset_derived_since((NOW - timedelta(hours=6)).isoformat())

    assert dropped["messages"] == 1                     # only the recent post
    # old post keeps its event + cluster and stays parsed
    assert db.query_one(
        "SELECT parsed_at FROM raw_message WHERE msg_id=1")["parsed_at"] is not None
    assert db.query_one("SELECT COUNT(*) n FROM event")["n"] == 1
    # recent post is re-queued
    assert db.query_one(
        "SELECT parsed_at FROM raw_message WHERE msg_id=2")["parsed_at"] is None


def test_reset_since_rebuilds_straddling_cluster_in_full(db, deduper):
    """A cluster that starts before the cutoff but is still active after it
    gets every one of its posts re-queued, not just the recent ones."""
    t0 = (NOW - timedelta(hours=8)).isoformat()
    t1 = (NOW - timedelta(hours=7, minutes=55)).isoformat()
    _store(db, deduper, "a", 1, t0, _ev(place_name="Бровари", lat=50.51, lon=30.79))
    _store(db, deduper, "a", 2, t1, _ev(place_name="Бровари", lat=50.52, lon=30.80))
    assert db.query_one("SELECT COUNT(*) n FROM cluster")["n"] == 1

    dropped = db.reset_derived_since((NOW - timedelta(hours=7, minutes=57)).isoformat())

    assert dropped["clusters"] == 1
    assert dropped["messages"] == 2                     # both, though msg 1 predates cutoff
    assert db.query_one("SELECT COUNT(*) n FROM cluster")["n"] == 0
    assert db.query_one("SELECT COUNT(*) n FROM event")["n"] == 0


# -------------------------------------------------- Service.reparse_since
def test_service_reparse_since_reparses_window(tmp_path, monkeypatch):
    monkeypatch.setenv("DRONEVIS_DB_PATH", str(tmp_path / "svc.db"))
    cfg = load_config()
    svc = Service(cfg)
    try:
        old = (NOW - timedelta(hours=30)).isoformat()
        recent = (NOW - timedelta(hours=1)).isoformat()
        svc.db.upsert_raw_message("war_monitor", 1, "u1", old,
                                  "БпЛА курсом на Бровари")
        svc.db.upsert_raw_message("war_monitor", 2, "u2", recent,
                                  "Балістика на Київ")
        svc.reparse_all()
        ev_before = svc.db.query_one("SELECT COUNT(*) n FROM event")["n"]
        assert ev_before >= 1

        res = svc.reparse_since(6)

        assert res["messages"] == 1                     # only the recent post
        # nothing lost - old event still present, recent one rebuilt
        assert svc.db.query_one("SELECT COUNT(*) n FROM event")["n"] == ev_before
    finally:
        import asyncio
        asyncio.run(svc.aclose())
