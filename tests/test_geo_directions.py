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
        # Київ is the target; the drone is outside it on the north side,
        # coming in - not on the city centre (which made the course
        # projection run on past the city)
        assert e.dest_name == "Київ"
        assert e.place_name == "Київ (з півночі)"
        assert e.lat > e.dest_lat and abs(e.lon - e.dest_lon) < 0.01
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

    def test_clear_message_makes_clear_markers(self, parse):
        evs = parse("Дніпро та Полтава чисто, лунали вибухи.")
        assert evs and all(e.threat_type == "clear" for e in evs)

    def test_clear_ignored_when_line_also_has_a_threat(self, parse):
        (e,) = parse("Бровари: 3х шахеди курсом на Київ, потім тихо чисто")
        assert e.threat_type == "shahed"


def test_toward_the_city_is_the_target_not_the_position():
    """kpszsu "Київ: реактивний БпЛА в напрямку міста з півночі": "the city"
    is the header's city and the destination; the course ends there."""
    from dronevis.config import load_config
    from dronevis.geo.util import haversine_km
    from dronevis.parse.pipeline import APPROACH_KM, Parser

    p = Parser(load_config())
    (e,) = p.parse("🏍 Київ: реактивний БпЛА в напрямку міста з півночі", channel="kpszsu")
    assert e.threat_type == "jet_uav"
    assert e.dest_name == "Київ"
    km = haversine_km((e.lat, e.lon), (e.dest_lat, e.dest_lon))
    assert abs(km - APPROACH_KM) < 0.5 and e.lat > e.dest_lat
    # "над містом" is still *over* the city
    (o,) = p.parse("Київ: БпЛА над містом", channel="kpszsu")
    assert o.place_name == "Київ" and o.dest_name is None


def test_cone_stops_at_the_target():
    """The projected course of an inbound track ends at its target instead
    of running on 90 km past it."""
    from datetime import datetime, timezone

    from dronevis.geo.util import haversine_km
    from dronevis.predict import Fix, assess, cone, estimate_motion

    t = datetime(2026, 10, 10, 21, 36, tzinfo=timezone.utc)
    kyiv = (50.45, 30.52)
    north = (50.585, 30.52)                       # 15 km north
    m = estimate_motion([Fix(t, *north)], family="drone", status="moving",
                        cruise_kmh=560, reported_heading=180.0, dest=kyiv)
    far = max(haversine_km(north, pt) for pt in cone(m))
    assert far <= 15.5
    a = assess(m, kyiv, t)
    assert a["toward"] and not a["receding"] and a["level"] == "high"


def test_no_cone_for_drones_or_whole_city_fixes():
    """Drones get no forecast wedge; nothing projects a course from a fix on
    a whole big city ("Київ"), while a district fix ("Позняки") still may."""
    from datetime import datetime, timezone

    from dronevis.api import _cluster_dto

    t = datetime(2026, 10, 10, 21, 36, tzinfo=timezone.utc)
    iso = t.isoformat()

    def dto(threat, place, lat, lon):
        c = {"id": 1, "threat_type": threat, "status": "moving", "first_posted_at": iso,
             "last_posted_at": iso, "centroid_lat": lat, "centroid_lon": lon,
             "place_name": place, "dest_name": None, "dest_lat": None, "dest_lon": None,
             "heading_deg": 180.0, "count": 1, "count_max": 1, "event_count": 1,
             "channels": "[]", "altitude_m": None, "resolved_at": None}
        e = {"lat": lat, "lon": lon, "posted_at": iso, "altitude_m": None,
             "channel": "kpszsu", "url": "u", "raw_line": "", "parse_confidence": 0.9,
             "r_posted": iso, "count": 1, "status": "moving"}
        return _cluster_dto(c, [e], now=t, coarse_places={"Київ"})["motion"]

    assert dto("jet_uav", "Позняки", 50.40, 30.63)["cone"] == []          # drone
    kyiv = dto("cruise_missile", "Київ", 50.45, 30.52)
    assert kyiv["cone"] == [] and kyiv["projection"] is False and kyiv["now"] is None
    district = dto("cruise_missile", "Позняки", 50.40, 30.63)
    assert len(district["cone"]) > 2 and district["projection"] is True
