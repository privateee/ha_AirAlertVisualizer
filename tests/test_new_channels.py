"""kyiv_golovne, horizon_of_war_official and eradarrua (added 2026-10-15):
real posts from each, and what they must become."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from dronevis.config import load_config
from dronevis.parse.pipeline import Parser


@pytest.fixture(scope="module")
def parser():
    return Parser(load_config())


def _places(parser, text, channel):
    return [(e.threat_type, e.place_name) for e in parser.parse(text, channel=channel)]


# -- kyiv_golovne: a news channel that signs its news, not its tracking ----
def test_signed_news_post_is_skipped(parser):
    news = ("❗️😠 росіяни здійснили одну з найбільших атак дронами, намагаючись скористатися "
            "проблемами зі Starlink в Україні, — Зеленський\n\n🔴 Київ Головне\nНадіслати новину | Наш чат")
    assert _places(parser, news, "kyiv_golovne") == []


def test_unsigned_tracking_post_is_read(parser):
    assert _places(parser, "❗️ Ірпінь - Буча - Ворзель увага дрон до вас!", "kyiv_golovne")[0] \
        == ("shahed", "Ірпінь")


def test_a_surname_is_not_a_village(parser):
    # "Зеленський" was a keyboard slip away from Зелене (Kherson oblast)
    evs = parser.parse("Атака дронами, — Зеленський", channel="kpszsu")
    assert all(e.place_name is None or "Зелене" not in e.place_name for e in evs)


def test_geran_5_is_the_jet_one(parser):
    assert _places(parser, "😨 На Черкащину залітає 4х герані-5", "kyiv_golovne")[0][0] == "jet_uav"


# -- horizon_of_war_official: Chernihiv villages, siren-status posts -------
@pytest.mark.parametrize("text", [
    "🟡 Ніжинський район — повітряна тривога, жовтий рівень: Дронова загроза (жовтий рівень)",
    "🟢 Ніжинський район — відбій повітряної тривоги\n⚠️ Зверніть увагу, повітряна тривога досі "
    "триває у:\n- Корюківський район",
    "🟢 10:10 м.Київ - відбій повітряної тривоги!\nДронова загроза (жовтий рівень)",
])
def test_siren_status_posts_are_not_sightings(parser, text):
    assert parser.parse(text, channel="horizon_of_war_official") == []


def test_chernihiv_villages_resolve(parser):
    text = ("Чернігівщина\nРеактивний 🛵 повз Вересоч на Дрімайлівку\n"
            "Реактивний 🛵 на Сеньківку\nРеактивний 🛵 повз Старі Боровичі")
    got = [e.place_name for e in parser.parse(text, channel="horizon_of_war_official")]
    # these were "Волока", "Борова" (Kharkiv), "Берестове" (Donetsk) - or nothing
    assert got == ["Вересоч", "Сеньківка (Чернігівська)", "Старі Боровичі"]


# -- eradarrua: terse - a bare district name is a sighting ----------------
def test_bare_district_names_are_sightings(parser):
    assert _places(parser, "Оболонь\nТеличка", "eradarrua") == [
        ("unknown", "Оболонь"), ("unknown", "Теличка")]
    assert _places(parser, "Ударний на Кременчук", "eradarrua")[0] == ("shahed", "Кременчук")


def test_list_lines_under_a_drone_header(parser):
    got = _places(parser, "🛵 Київщина\n- 1 на Трипілля\n- 1 на Яготин", "eradarrua")
    assert [p for _, p in got] == ["Трипілля", "Яготин"]


# -- replies that end a drone -------------------------------------------
@pytest.mark.parametrize("reply, ends", [("Здох", True), ("Впала", True),
                                         ("По ньому все", True), ("1 знищили", True),
                                         ("Знову на Бориспіль", False)])
def test_gone_reply_ends_the_quoted_drone(tmp_path, monkeypatch, reply, ends):
    from dronevis.service import Service

    monkeypatch.setenv("DRONEVIS_DB_PATH", str(tmp_path / "g.db"))
    svc = Service(load_config())
    ch = "horizon_of_war_official"
    t0 = datetime(2026, 10, 14, 19, 23, tzinfo=timezone.utc)
    post = "Чернігівщина\nРеактивний 🛵 на Ніжин"
    try:
        rid, _ = svc.db.upsert_raw_message(ch, 97244, "u", t0.isoformat(), post)
        svc._parse_and_store(rid, ch, post, t0.isoformat())
        later = (t0 + timedelta(minutes=3)).isoformat()
        rid2, _ = svc.db.upsert_raw_message(ch, 97245, "u2", later, reply, 97244)
        svc._parse_and_store(rid2, ch, reply, later, reply_to=97244)
        open_ = svc.db.query_one("SELECT COUNT(*) n FROM cluster WHERE resolved_at IS NULL")["n"]
        assert (open_ == 0) is ends
    finally:
        asyncio.run(svc.aclose())


def test_district_alert_colour_is_not_a_sighting(parser):
    text = "Вишгородський район жовта тривога (дронова небезпека)!"
    assert parser.parse(text, channel="kyiv_golovne") == []


# -- a bare "водосховище" is the reservoir near the rest of the post --------
@pytest.mark.parametrize("text, want", [
    ("Реактивний на Дніпровське / водосховище", "Київське водосховище"),
    ("Шахед повз Світловодськ на водосховище", "Кременчуцьке водосховище"),
])
def test_bare_reservoir_is_the_nearby_one(parser, text, want):
    ev = parser.parse(text, channel="horizon_of_war_official")[0]
    assert want in (ev.place_name, ev.dest_name)


def test_bare_reservoir_with_nothing_near_is_nowhere(parser):
    evs = parser.parse("Шахед над водосховищем", channel="kpszsu")
    assert all(e.place_name is None or "водосховище" not in e.place_name for e in evs)
    evs = parser.parse("Шахед над водосховищем", channel="kpszsu", area_center=(50.45, 30.52))
    assert evs[0].place_name == "Київське водосховище"


# -- an untyped "ракета" from a launch area is ballistic (minutes, not 40) ---
@pytest.mark.parametrize("text, want", [
    ("Курськ ракета", "ballistic"), ("Ракета з Брянська", "ballistic"),
    ("Курськ Х-59", "cruise_missile"), ("Крилата ракета з Курська", "cruise_missile"),
    ("Ракета на Київ", "cruise_missile"),
])
def test_untyped_missile_from_a_launch_area(parser, text, want):
    assert parser.parse(text, channel="eradarrua")[0].threat_type == want


@pytest.mark.parametrize("region", ["Вінничині", "Вінниччині"])
def test_one_ch_region_spelling_steers_the_homonym(parser, region):
    # kpszsu writes "Вінничині"; only "Вінниччина" was known, so Калинівка
    # went to the Kyiv-oblast one
    ev = parser.parse(f"Реактивний БпЛА на {region}, між н.п.Калинівка та Стрижавка",
                      channel="kpszsu", area_center=(50.45, 30.52))[0]
    assert ev.place_name == "Калинівка (Вінницька)"


# -- "острів" is an island, not a declined Остер -----------------------------
@pytest.mark.parametrize("text, want", [
    ("6× — На висоті понад 6 км на Рибальський острів", "Рибальський острів"),
    ("Шахед над Трухановим островом", "Труханів острів"),
    ("Шахед повз Остра на Козелець", "Остер"),
])
def test_island_is_not_oster(parser, text, want):
    assert parser.parse(text, channel="kyiv_golovne", area_center=(50.45, 30.52))[0].place_name == want


def test_bare_island_word_is_nowhere(parser):
    assert parser.parse("Шахед над островом", channel="kyiv_golovne") == []


# -- "друга / інший / ще одна": a different drone ---------------------------
def test_other_one_after_a_comma_is_its_own_report(parser):
    evs = parser.parse("Жуляни далі ви, інший на Боярку", channel="kyiv_golovne",
                       area_center=(50.45, 30.52))
    assert [(e.place_name, e.dest_name, e.another) for e in evs] == [
        ("Жуляни", None, False), ("Боярка", None, True)]


@pytest.mark.parametrize("text", ["На висоті 6.5 км на Нижні Сади (це друга Герань)",
                                  "Ще одна Герань-5 на Козин залітає"])
def test_second_drone_is_marked(parser, text):
    assert parser.parse(text, channel="kyiv_golovne")[0].another
