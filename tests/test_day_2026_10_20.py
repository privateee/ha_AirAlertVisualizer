"""Real posts from the 2026-10-19/20 day replay: where they must land, and
which reports are one drone."""

from datetime import datetime, timedelta, timezone

import pytest

from dronevis.config import load_config
from dronevis.db import Database
from dronevis.dedupe import Deduper
from dronevis.parse.pipeline import ParsedEvent, Parser

KYIV = (50.45, 30.52)


@pytest.fixture(scope="module")
def parser():
    return Parser(load_config())


def _got(parser, text, channel):
    return [(e.threat_type, e.place_name) for e in parser.parse(text, channel=channel,
                                                                area_center=KYIV)]


# -- placement -------------------------------------------------------------
def test_a_line_keeps_its_own_region(parser):
    text = ("🏍 Реактивний БпЛА на заході Чернігівщини, курс на Київщину.\n"
            "🏍 Реактивний БпЛА на півдні Миколаївщини, курс на північ.")
    assert [p for _, p in _got(parser, text, "kpszsu")] == [
        "Чернігівська обл.", "Миколаївська обл."]


def test_region_heading_is_a_heading_and_moped_means_shahed(parser):
    text = "🛵 Волинь\n- 1 на Ковель\n\n🛵 Житомирщина\n- 3 повз Олевськ на Рівненщину"
    assert _got(parser, text, "eradarrua") == [("shahed", "Ковель"), ("shahed", "Олевськ")]


@pytest.mark.parametrize("text", ["Маневри по висоті", "Трохи знизився"])
def test_ordinary_words_are_not_places(parser, text):
    assert parser.parse(text, channel="eradarrua") == []


@pytest.mark.parametrize("text, want", [
    ("Бучмани", "Бучмани"), ("Реактивний → Литвинівка, Літки", "Литвинівка"),
    ("Одещина: реактивний БпЛА курсом на Ананьїв.", "Ананьїв"),
])
def test_villages_that_were_typo_matched_elsewhere(parser, text, want):
    assert parser.parse(text, channel="eradarrua")[0].place_name == want


def test_rembaza_is_on_the_east_bank(parser):
    ev = parser.parse("Рембаза", channel="eradarrua")[0]
    assert ev.lon > 30.6                    # was 30.40, by Нивки on the west bank


@pytest.mark.parametrize("text", [
    "Яким чином ТЕЦ Києва сприяють терактам , коняка не пояснив.",
    "Київ так само атакують, використовуючи одні і ті ж коридори (маршрути). Місяцями.",
    "03:07 м.Київ - повітряна тривога!",
    "Тимчасово дорозвідка у столиці.",
])
def test_talk_and_status_are_not_sightings(parser, text):
    assert parser.parse(text, channel="eradarrua") == []


@pytest.mark.parametrize("channel, text", [
    ("eradarrua", "Полетів на Житомирщину, буде відбій у столиці"),
    ("kyiv_golovne", "Герань-5 пролетіла над Києвом та пішла на Житомирщину"),
])
def test_leaving_the_city_clears_it(parser, channel, text):
    assert _got(parser, text, channel) == [("clear", "Київ")]


def test_news_channel_first_line_is_a_report_not_a_heading(parser):
    got = _got(parser, "1 Хотянівка\n1 Переяслав\n1 в зоні\n\nДетальніше по руху безпілотників ⬅️",
               "kievreal1")
    assert [p for _, p in got] == ["Хотянівка", "Переяслав"]
    evs = parser.parse("На Пущу-Водицю йде, знову набрав висоту\nЩе 1 на Ржищів",
                       channel="kievreal1")
    assert [(e.place_name, e.another) for e in evs] == [("Пуща-Водиця", False), ("Ржищів", True)]


# -- identity ----------------------------------------------------------------
@pytest.fixture
def deduper(tmp_path):
    db = Database(tmp_path / "d.db")
    yield db, Deduper(db, load_config())
    db.close()


def _put(db, dd, channel, mid, when, place, lat, lon, threat="unknown", conf=0.8):
    rid, _ = db.upsert_raw_message(channel, mid, f"u{mid}", when.isoformat(), "t")
    ev = ParsedEvent(threat_type=threat, threat_raw=None, count=None, status="moving",
                     place_name=place, lat=lat, lon=lon, geo_confidence=conf,
                     raw_line=place, parse_confidence=0.8)
    row = ev.to_row()
    row.update(raw_message_id=rid, channel=channel, posted_at=when.isoformat())
    return dd.assign(db.insert_event(row), ev, channel, when.isoformat())


T = datetime(2026, 10, 20, 15, 53, tzinfo=timezone.utc)


def test_nameless_next_fix_continues_the_only_track_in_reach(deduper):
    db, dd = deduper
    c1 = _put(db, dd, "kpszsu", 1, T, "Бориспіль", 50.35, 30.95, "jet_uav", 0.9)
    # 21 km on, 1.5 min later, another channel, no type: that jet moving on
    c2 = _put(db, dd, "eradarrua", 2, T + timedelta(seconds=90), "Рембаза", 50.443, 30.66)
    assert c1 == c2


def test_one_drone_started_twice_is_joined(deduper):
    db, dd = deduper
    a = _put(db, dd, "kpszsu", 1, T, "Дарниця", 50.43, 30.62, "jet_uav", 0.9)
    b = _put(db, dd, "eradarrua", 2, T, "Харківський масив", 50.40, 30.64)  # same minute, 3.6 km
    # if they started apart, a report fitting both equally joins them
    c = _put(db, dd, "kyiv_golovne", 3, T + timedelta(minutes=2), "Оболонь", 50.50, 30.50)
    d = _put(db, dd, "eradarrua", 4, T + timedelta(minutes=2, seconds=10), "Оболонь", 50.50, 30.50)
    assert len({a, b, c, d} & {r["id"] for r in db.query("SELECT id FROM cluster")}) == 1


def test_two_drones_that_meet_are_not_joined(deduper):
    db, dd = deduper
    # one over Чернігівщина, one west of Kyiv, at the same time
    n = _put(db, dd, "horizon_of_war_official", 1, T, "Березна", 51.57, 31.78, "jet_uav", 0.9)
    w = _put(db, dd, "eradarrua", 2, T + timedelta(seconds=30), "Буча", 50.55, 30.21, "jet_uav", 0.9)
    assert n != w
    ids = {r["id"] for r in db.query("SELECT id FROM cluster")}
    assert {n, w} <= ids
