"""Summary-by-type batch posts ("Загальна по мопедам:" + one line per group) -
real messages like this have no per-line threat word and rely on the header
to declare the type. Covers:
  - the header itself produces no phantom event
  - every line inherits the header's threat type unless it states its own
  - "в районі X" / "південніше X" / bare "курс <cardinal>" lines, which have
    no verb at all, still count as reports
  - a couple of gazetteer declension/alias gaps found via a real post, in
    both Ukrainian and Russian
"""

import pytest

from dronevis.parse.pipeline import Parser
from dronevis.config import load_config

KYIV = (50.4501, 30.5234)


@pytest.fixture(scope="module")
def parser():
    return Parser(load_config())


def _p(parser, text, channel=None):
    return parser.parse(text, channel=channel, area_center=KYIV)


def test_header_line_alone_produces_no_event(parser):
    evs = _p(parser, "Загальна по мопедам:\n3 курсом на Ковель")
    assert len(evs) == 1                       # header line itself: nothing
    assert evs[0].place_name == "Ковель"


def test_lines_without_their_own_keyword_inherit_header_type(parser):
    txt = (
        "Загальна по мопедам:\n"
        "3 курсом на Ковель\n"
        "4 в районі Коростеня, курс західний\n"
    )
    evs = _p(parser, txt)
    assert len(evs) == 2
    assert {e.threat_type for e in evs} == {"shahed"}


def test_explicit_type_on_a_line_overrides_the_header(parser):
    txt = "Загальна по мопедам:\n3 курсом на Ковель\n1 іскандер на Суми"
    evs = _p(parser, txt)
    types = {e.place_name: e.threat_type for e in evs}
    assert types["Ковель"] == "shahed"
    assert types["Суми"] == "iskander"


@pytest.mark.parametrize("text,slug,place", [
    ("Загальна по мопедам:\n4 в районі Коростеня, курс західний", "shahed", "Коростень"),
    ("Загальна по мопедам:\n15 крутяться в районі Куликівки", "shahed", "Куликівка"),
    ("Загальна по мопедам:\n4 південніше Конотопа, курс західний", "shahed", "Конотоп"),
    ("Загальна по мопедам:\n4 южнее Конотопа, курс западный", "shahed", "Конотоп"),
])
def test_verbless_positional_lines_still_produce_events(parser, text, slug, place):
    (e,) = [e for e in _p(parser, text) if e.place_name]
    assert e.threat_type == slug
    assert e.place_name == place


def test_bare_course_word_sets_heading_without_flipping_it(parser):
    """'курс західний' names the heading directly (270) - it must not be
    treated like 'з заходу' ("from the west", which would invert to 90)."""
    (e,) = [e for e in _p(parser, "Загальна по мопедам:\n4 в районі Коростеня, курс західний")
            if e.place_name]
    assert e.heading_deg == 270


def test_source_to_dest_chain_across_two_places(parser):
    txt = "Загальна по мопедам:\n10 з боку Мени летять у бік Куликівки Чернігівської області"
    (e,) = [e for e in _p(parser, txt) if e.place_name]
    assert e.place_name == "Мена"
    assert e.dest_name == "Куликівка"
    assert e.heading_deg is not None


# ---- gazetteer gaps found via the real post ----------------------------
def test_short_vowel_ending_place_declines(parser):
    """"Мена" is only 4 letters; the declension engine used to require 5."""
    (e,) = [e for e in _p(parser, "Загальна по мопедам:\n2 з боку Мени") if e.place_name]
    assert e.place_name == "Мена"


def test_bila_tserkva_ukrainian_genitive_alias(parser):
    (e,) = [e for e in _p(parser, "Загальна по мопедам:\n3 південніше Білої Церкви, курс західний")
            if e.place_name]
    assert e.place_name == "Біла Церква"


def test_comparative_direction_word_is_not_mistaken_for_a_place(parser):
    """"південніше"/"північніше"/etc. declining "Південне" (a real Kharkiv
    obl. village) to "південні" must not let the wildcard case-ending
    matcher swallow "ше" and steal the line for every "південніше <place>"
    report - it must resolve to the place actually named."""
    (e,) = [e for e in _p(parser, "Загальна по мопедам:\n4 південніше Конотопа, курс західний")
            if e.place_name]
    assert e.place_name == "Конотоп"


def test_oblast_adjective_does_not_falsely_resolve_via_a_short_alias(parser):
    """"Черкаської області" used to false-positive-match the city Черкаси
    through its short genitive-plural alias "черкас" + the case-ending
    wildcard swallowing "кої" - promoting an oblast-only reference to a
    precise (wrong-ish) destination point."""
    evs = _p(parser, "Загальна по мопедам:\n1 пролетів Лубни до Черкаської області")
    (e,) = [e for e in evs if e.place_name]
    assert e.place_name == "Лубни"
    assert e.dest_name != "Черкаси"


def test_bila_tserkva_russian_genitive_alias(parser):
    (e,) = [e for e in _p(parser, "Загальна по мопедам:\n3 южнее Белой Церкви, курс западный")
            if e.place_name]
    assert e.place_name == "Біла Церква"


def test_nova_odesa_not_confused_with_odesa(parser):
    """"Новой Одессы" (genitive of Нова Одеса, Mykolaiv obl.) must not
    resolve to plain Одеса, ~130 km away."""
    txt = "2 керованих реактивних летять у бік Нової Одеси Миколаївської області"
    (e,) = [e for e in _p(parser, txt) if e.place_name]
    assert e.place_name == "Нова Одеса"

    txt_ru = "2 управляемых реактивных летят в сторону Новой Одессы Николаевской области"
    (e,) = [e for e in _p(parser, txt_ru) if e.place_name]
    assert e.place_name == "Нова Одеса"
