"""How the channels really write (from ~4000 posts of their history):
shortened / nicknamed places, adjectives instead of names, slang, typos."""

import pytest

from dronevis.config import load_config
from dronevis.geo.gazetteer import Gazetteer
from dronevis.parse.normalize import fold
from dronevis.parse.pipeline import Parser

KYIV = (50.4501, 30.5234)


@pytest.fixture(scope="module")
def gaz():
    return Gazetteer()


@pytest.fixture(scope="module")
def parser(gaz):
    return Parser(load_config(), gaz)


def _places(gaz, text):
    return [h.place.name for h in gaz.find(fold(text), area_center=KYIV)]


@pytest.mark.parametrize("text,place", [
    ("🏍 Реактивний БпЛА на Запоріжжя", "Запоріжжя"),     # was blocked by the oblast word
    ("у Запоріжжі вибухи", "Запоріжжя"),
    ("Троя реактив", "Троєщина"),
    ("Перша залітає у Кроп", "Кропивницький"),
    ("Пуща Водиця падає!!", "Пуща-Водиця"),
    ("КАБы подлетают к Кривому Рогу", "Кривий Ріг"),
    ("з Черкас", "Черкаси"),                             # plural-name genitive
    ("біля Прилук", "Прилуки"),
    ("повз Лісники", "Лісники (Київ)"),                   # villages the posts name
    ("курсом на Теплик", "Теплик"),
    ("на Затоку", "Затока"),
    ("Реактив Бровари від Бобрика залітає", "Бровари"),
])
def test_place_forms(gaz, text, place):
    assert place in _places(gaz, text)


@pytest.mark.parametrize("text,place", [
    ("Реактивний БпЛА у Миколаївському районі", "Миколаїв"),
    ("1х реактивний Сікер Броварський район", "Бровари"),
    ("у напрямку Білоцерківського району Київщини", "Біла Церква"),
    ("у Кременчуцькому районі", "Кременчук"),
    ("мопед в Баштанском районе", "Баштанка"),
    ("у Синельниківському районі", "Синельникове"),
])
def test_district_adjective_is_its_centre_town(gaz, text, place):
    assert _places(gaz, text) == [place]


@pytest.mark.parametrize("text,label", [
    ("у Черкаській області", "Черкаська обл."),
    ("в Сумской области", "Сумська обл."),
    ("на Полтавскую область", "Полтавська обл."),
    ("2х КР з Криму у напрямку Одеського району", "Крим"),
    ("10+ БпЛА з акваторії Чорного моря", "Чорне море"),
    ("Над рб на Прип'ять реактив", "Білорусь"),
])
def test_regions_and_origins(gaz, text, label):
    assert gaz.region_hit(fold(text))[0] == label


def test_oblast_adjective_is_not_the_city(gaz):
    assert _places(gaz, "у Черкаській області") == []


@pytest.mark.parametrize("typo,place", [
    ("Ьориспіль", "Бориспіль"),          # ь is next to б
    ("Святощин", "Святошин"),            # щ next to ш
    ("Виноградао", "Виноградар"),
])
def test_keyboard_typos(gaz, typo, place):
    assert gaz.typo_place(typo, area_center=KYIV).name == place


def test_a_missing_letter_is_never_guessed(gaz):
    # a dropped letter is not a keyboard slip; guessing those is how
    # "Лісники" turned into a different real village ("Лиснівка")
    assert gaz.typo_place("Бориспль", area_center=KYIV) is None


@pytest.mark.parametrize("text,status", [
    ("Київ: реактив Борщагівки жахнуло.", "impact"),
    ("Київ: приліт по Оболоні, реактив", "impact"),        # "приліт" folds to "прилит"
    ("Теличка падає 700м! реактив", "descending"),
])
def test_slang_status(parser, text, status):
    assert parser.parse(text, channel="AerisRimor")[0].status == status


def test_minus_is_all_clear_for_the_place(parser):
    (e,) = parser.parse("Виноградар мінус", channel="war_monitor")
    assert e.threat_type == "clear" and e.place_name == "Виноградар"


@pytest.mark.parametrize("text,heading", [
    ("🏍 Реактивний БпЛА у Миколаївському районі ➡️ у північному напрямку", 0),
    ("🏍 Реактивний БпЛА у Кропивницькому районі повз Знам'янку у західному напрямку", 270),
    ("Реактивний БпЛА у Синельниківському районі у північно-західному напрямку", 315),
])
def test_direction_word(parser, text, heading):
    assert parser.parse(text, channel="kpszsu")[0].heading_deg == heading


def test_course_without_na(parser):
    (e,) = parser.parse("🏍 Реактивний БпЛА на Вінничині, курс Теплик", channel="kpszsu")
    assert e.place_name == "Теплик" or e.dest_name == "Теплик"


def test_settlement_prefix(parser):
    (e,) = parser.parse("🏍Реактивні БпЛА на Вінничині, в напрямку н.п.Теплик",
                        channel="kpszsu")
    assert "Теплик" in (e.place_name, e.dest_name)


def test_typo_reaches_the_map(parser):
    (e,) = parser.parse("Реактив на Ьориспіль", channel="AerisRimor",
                        area_center=KYIV)
    assert "Бориспіль" in (e.place_name, e.dest_name)
