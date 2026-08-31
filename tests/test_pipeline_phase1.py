"""Phase-1 behaviours: summary filtering, all-clear resolve, area-by-dest,
sub-type slugs end to end."""

from datetime import datetime, timedelta, timezone

import pytest

from dronevis.areas import cluster_touches_area
from dronevis.config import Area, load_config
from dronevis.db import Database
from dronevis.dedupe import Deduper
from dronevis.parse.pipeline import ParsedEvent, Parser, is_summary
from dronevis.parse.normalize import fold

KYIV = (50.4501, 30.5234)


@pytest.fixture(scope="module")
def parser():
    return Parser(load_config())


def _p(parser, text, channel=None):
    return parser.parse(text, channel=channel, area_center=KYIV)


# ---- summary posts ---------------------------------------------------------
@pytest.mark.parametrize("text", [
    "За ніч по Україні збито 45 ворожих БпЛА та 3 крилаті ракети.",
    "Станом на 08:00 зафіксовано 12 пусків КАБів по Харківщині.",
    "Підсумки нічної атаки: пошкоджено енергооб'єкт.",
])
def test_summary_posts_make_no_markers(parser, text):
    assert _p(parser, text) == []
    assert is_summary(fold(text))


def test_live_multi_threat_post_is_not_a_summary(parser):
    txt = "Загальна:\n3х шахеди повз Ніжин курсом на Київ\n2х реактиви на Бровари"
    evs = _p(parser, txt)
    assert len(evs) >= 2 and not is_summary(fold(txt))


# ---- sub-types ----------------------------------------------------------
@pytest.mark.parametrize("text,slug", [
    ("Бандероль курсом на Обухів", "banderol"),
    ("Кинжал по аеродрому в Києві", "kinzhal"),
    ("Х-101 на Львів", "x101"),
    ("2 іскандери на Дніпро", "iskander"),
])
def test_subtype_slug_flows_through(parser, text, slug):
    (e,) = _p(parser, text)
    assert e.threat_type == slug


# ---- all clear resolves clusters --------------------------------------
@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    yield d
    d.close()


def test_all_clear_resolves_nearby_cluster(db):
    dd = Deduper(db, load_config())
    t0 = datetime(2026, 8, 30, 22, 0, tzinfo=timezone.utc)

    def store(ev, when):
        rid, _ = db.upsert_raw_message("a", when.hour * 100 + when.minute,
                                       "u", when.isoformat(), "x")
        row = ev.to_row()
        row.update(raw_message_id=rid, channel="a", posted_at=when.isoformat())
        return dd.assign(db.insert_event(row), ev, "a", when.isoformat())

    cid = store(ParsedEvent(
        threat_type="shahed", threat_raw="шахед", count=2, status="moving",
        place_name="Бровари", lat=50.51, lon=30.79, geo_confidence=0.9,
        raw_line="шахед над броварами", parse_confidence=0.9), t0)
    assert db.query_one("SELECT resolved_at FROM cluster WHERE id=?", (cid,))["resolved_at"] is None

    store(ParsedEvent(
        threat_type="clear", threat_raw=None, count=None, status="clear",
        place_name="Бровари", lat=50.51, lon=30.79, geo_confidence=0.8,
        raw_line="бровари чисто", parse_confidence=0.7), t0 + timedelta(minutes=8))
    assert db.query_one("SELECT resolved_at FROM cluster WHERE id=?", (cid,))["resolved_at"] is not None


# ---- area filter matches destination ---------------------------------
def test_cluster_in_area_by_destination():
    area = Area(key="k", label="Kyiv", center=KYIV, radius_km=60)
    # missile still 300 km away but declared heading for Kyiv
    c = {"centroid_lat": 49.0, "centroid_lon": 33.0,
         "dest_lat": 50.45, "dest_lon": 30.52}
    assert cluster_touches_area(area, c)
    c2 = {"centroid_lat": 49.0, "centroid_lon": 33.0,
          "dest_lat": 46.9, "dest_lon": 32.0}
    assert not cluster_touches_area(area, c2)
