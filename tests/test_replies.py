"""Replies: t.me shows the quoted post's text first; a channel replying to
its own report is saying that drone moved on."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from dronevis.config import load_config
from dronevis.db import Database
from dronevis.dedupe import Deduper
from dronevis.ingest.tme_web import _parse_page
from dronevis.parse.pipeline import ParsedEvent

FIXTURE = Path(__file__).parent / "data" / "tme_reply.html"


def test_reply_keeps_its_own_text_and_link():
    """Real kpszsu page: 85007 replies to 85006. The reply's own text is the
    new report; before, it was saved as a copy of the quote."""
    posts = {p.msg_id: p for p in _parse_page(FIXTURE.read_text(encoding="utf-8"), "kpszsu")}
    # kpszsu chains them: 85004 (Теплик) <- 85006 (Козятин) <- 85007 - one drone
    assert "Козятина" in posts[85006].text and posts[85006].reply_to == 85004
    r = posts[85007]
    assert "Рівненщини" in r.text and "Козятина" not in r.text
    assert r.reply_to == 85006


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "r.db")
    yield d
    d.close()


def _ev(place, lat, lon, threat="jet_uav"):
    return ParsedEvent(threat_type=threat, threat_raw="", count=None, status="moving",
                       place_name=place, lat=lat, lon=lon, geo_confidence=0.6,
                       raw_line=place, parse_confidence=0.85)


def _store(db, dd, mid, ev, when, follow=None):
    rid, _ = db.upsert_raw_message("kpszsu", mid, f"u/{mid}", when, ev.raw_line)
    row = ev.to_row()
    row.update(raw_message_id=rid, channel="kpszsu", posted_at=when)
    return dd.assign(db.insert_event(row), ev, "kpszsu", when, follow=follow)


def test_reply_continues_the_quoted_track(db):
    """Козятин (Vinnytsia obl.) -> 'via Khmelnytska toward Rivnenska' is
    ~130 km in 25 min: too far for the distance rules on their own, but the
    channel says it's the same drone by replying."""
    dd = Deduper(db, load_config())
    t0 = datetime(2026, 10, 10, 20, 46, tzinfo=timezone.utc)
    c1 = _store(db, dd, 85006, _ev("Козятин", 49.71, 28.83), t0.isoformat())
    t1 = (t0 + timedelta(minutes=25)).isoformat()
    lone = _store(db, dd, 99, _ev("Хмельницька обл.", 49.42, 26.98), t1)
    assert lone != c1                                      # no reply link: separate
    follow = [r["cluster_id"] for r in db.reply_clusters("kpszsu", 85006)]
    c2 = _store(db, dd, 85007, _ev("Хмельницька обл.", 49.42, 26.98), t1, follow=follow)
    assert c2 == c1


def test_reply_does_not_join_another_family(db):
    dd = Deduper(db, load_config())
    t0 = datetime(2026, 10, 10, 20, 0, tzinfo=timezone.utc).isoformat()
    c1 = _store(db, dd, 1, _ev("Козятин", 49.71, 28.83), t0)
    follow = [r["cluster_id"] for r in db.reply_clusters("kpszsu", 1)]
    c2 = _store(db, dd, 2, _ev("Київ", 50.45, 30.52, threat="ballistic"), t0, follow=follow)
    assert c2 != c1


def test_also_reply_adds_drones():
    from dronevis.service import adds_more
    assert adds_more("🏍 Також реактивні БпЛА у напрямку Макарова з півночі.")
    assert adds_more("🏍 Ще один реактивний БпЛА курсом на Дніпро.")
    assert not adds_more("🏍 Реактивний БпЛА через Хмельниччину у напрямку Рівненщини.")
