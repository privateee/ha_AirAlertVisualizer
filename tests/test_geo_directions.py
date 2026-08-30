"""Toponym resolution + direction/heading extraction."""

import pytest

from dronevis.geo.gazetteer import Gazetteer
from dronevis.geo.util import compass
from dronevis.parse.normalize import fold

KYIV = (50.4501, 30.5234)


@pytest.fixture(scope="module")
def gaz():
    return Gazetteer()


def test_declension_matches(gaz):
    for form in ["Бориспіль", "Борисполя", "Борисполі", "борисполь"]:
        hit = gaz.lookup(form, area_center=KYIV)
        assert hit and hit.place.name == "Бориспіль"


def test_local_slang_alias(gaz):
    hit = gaz.lookup("соф борщага", area_center=KYIV)
    assert hit and hit.place.name == "Софіївська Борщагівка"


def test_russian_form_resolves(gaz):
    hit = gaz.lookup("николаев", area_center=KYIV)
    assert hit and hit.place.name == "Миколаїв"


def test_oblast_header_detected(gaz):
    assert gaz.detect_oblast(fold("Полтавщина / Харківщина:")) == "poltava"
    assert gaz.detect_oblast(fold("на Запорізьку область")) == "zapor"


def test_ambiguous_name_resolved_by_oblast_hint(gaz):
    # bare "Семенівка" is ambiguous; the post's oblast header disambiguates
    hit = gaz.lookup("семенівка", oblast_hint="chernihiv", area_center=KYIV)
    assert hit and "Чернігівська" in hit.place.name
    hit = gaz.lookup("семенівка", oblast_hint="poltava", area_center=KYIV)
    assert hit and "Полтавська" in hit.place.name


class TestPipeline:
    def test_transit_and_destination(self, parse):
        (e,) = parse("Реактивний БпЛА повз Велику Димерку курсом на Бориспіль.")
        assert e.threat_type == "jet_uav"
        assert e.place_name == "Велика Димерка"
        assert e.dest_name == "Бориспіль"
        assert compass(e.heading_deg) in {"S", "SSE", "SSW"}

    def test_source_gives_heading(self, parse):
        (e,) = parse("3х реактиви з Брянщини на Славутич вздовж держкордону.")
        assert e.src_name in {"Брянськ", "Брянська обл. (рф)"}
        assert e.place_name == "Славутич"
        assert e.heading_deg is not None

    def test_specific_source_is_the_position(self, parse):
        # "from <specific place> to <dest>" -> position is the place, not dest
        (e,) = parse("1х реактив з київського водосховища на київ.")
        assert e.place_name == "Київське водосховище"
        assert e.dest_name == "Київ"
        assert compass(e.heading_deg) in {"S", "SSE", "SSW"}

    def test_cardinal_source_sets_heading(self, parse):
        (e,) = parse("реактивні БпЛА на Київ з півночі.")
        assert e.place_name == "Київ"
        assert compass(e.heading_deg) == "S"           # coming from the north
        (e2,) = parse("2 мопеда с юго-востока курсом на Миколаїв")
        assert compass(e2.heading_deg) in {"NW", "WNW", "NNW"}

    def test_circling_status(self, parse):
        (e,) = parse("1х реактив довкола Кропивницького.")
        assert e.status == "circling"
        assert e.place_name == "Кропивницький"

    def test_count_extracted(self, parse):
        (e,) = parse("2 реактива район Васильків.")
        assert e.count == 2
        assert e.place_name == "Васильків"

    def test_lone_destination_beats_vague_oblast(self, parse):
        # "Полтавщина ... на Скороходове" -> the concrete target is the
        # position; the oblast is only the direction it comes from
        (e,) = parse("Полтавщина:\n1х мгКР Бандероль на Скороходове.")
        assert e.place_name == "Скороходове"
        assert e.src_name == "Полтавська обл."
        assert e.heading_deg is not None

    def test_region_source_lone_dest(self, parse):
        (e,) = parse("Київщина реактивний БпЛА курсом на Обухів.")
        assert e.place_name == "Обухів"
        assert e.src_name == "Київська обл."
        assert e.dest_name is None

    def test_terse_channel_bare_toponym(self, parse):
        (e,) = parse("Соф борщага на Вишневе.", channel="AerisRimor")
        assert e.place_name == "Софіївська Борщагівка"
        assert e.dest_name == "Вишневе"

    def test_non_terse_channel_ignores_bare_toponym(self, parse):
        assert parse("Соф борщага на Вишневе.", channel="war_monitor") == []

    def test_clear_message_no_events(self, parse):
        assert parse("Дніпро та Полтава чисто, лунали вибухи.") == []
