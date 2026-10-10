"""kievreal1: a Kyiv city news channel that mixes drone tracking with news,
aftermath, ads and siren on/off posts. Samples are real posts (2026-10-09/10)."""

import pytest

from dronevis.config import load_config
from dronevis.parse.pipeline import Parser, strip_boilerplate

CH = "kievreal1"
FOOT = "\nㅤ \nНадіслати новину @novosti_kieva_bot\n👉ПІДПИСАТИСЯ"


@pytest.fixture(scope="module")
def parser():
    return Parser(load_config())


def first(parser, text):
    evs = parser.parse(text, channel=CH)
    return evs[0] if evs else None


@pytest.mark.parametrize("text", [
    # news / aftermath / ads / politics / siren status: never a map marker
    "В Оболонському районі відсутнє і холодне і гаряче водопостачання\n\n➡️ Дивитись ГРАФІКИ\nПІДПИСАТИСЬ",
    "Метро в Києві знову знеструмлено: поїзди припинили рух по зеленій гілці на правий берег від Осокорків, – ЗМІ" + FOOT,
    "🥃🍷Великі знижки на топові смаки осені на Rozetka\n\n💚 Jameson Irish Whiskey 0.7 л - 699 грн",
    "У Дніпровському районі уламки БпЛА впали на територію біля бізнес-центру. Горить автівка." + FOOT,
    "Народився під щасливою зіркою. Чоловік встиг втекти за секунду до удару безпілотника по дитячому садку в Бучанському районі" + FOOT,
    "\"Найслабша ланка\" - \"Укренерго\": експерт розповів, як провалили оперативний ремонт та захист підстанцій столиці",
    "Ми сказали Зеленському: не торкайся російського дизельного палива, — Трамп" + FOOT,
    "росія продовжує відрізати Київ від мережі і атакує ключові підстанції" + FOOT,
    "На Ірпінській набережній можна зустріти цілу компанію нутрій. Тварини вже звикли до людей та спокійно почуваються біля води." + FOOT,
    "🚨 М. КИЇВ - ОГОЛОШЕНА ПОВІТРЯНА ТРИВОГА" + FOOT,
    "❎ М. КИЇВ - ВІДБІЙ ТРИВОГИ" + FOOT,
    "Нові виходи, може до нас піти" + FOOT,
    "🚨 КИЇВСЬКА ОБЛАСТЬ - ОГОЛОШЕНА ПОВІТРЯНА ТРИВОГА\n\nБПЛА на Київщину" + FOOT,
])
def test_not_a_report(parser, text):
    assert parser.parse(text, channel=CH) == []


@pytest.mark.parametrize("text,slug,place,status", [
    ("Реактивний БпЛА курсом на Трипілля" + FOOT, "jet_uav", "Трипілля", "moving"),
    ("БПЛА на Конча-Заспу" + FOOT, "shahed", "Конча-Заспа", "moving"),
    ("🚨 М. КИЇВ - ОГОЛОШЕНА ПОВІТРЯНА ТРИВОГА\n\nБПЛА на Бучу" + FOOT, "shahed", "Буча", "moving"),
    # an edited post arrives as one line - the report must survive
    ("🚨 КИЇВСЬКА ОБЛАСТЬ - ОГОЛОШЕНА ПОВІТРЯНА ТРИВОГА  Бпла на Переяслав ㅤ  Надіслати новину "
     "@novosti_kieva_bot 👉ПІДПИСАТИСЯ", "shahed", "Переяслав", "moving"),
    # bare places and short status words are sightings in this channel
    ("Вишгород" + FOOT, "unknown", "Вишгород", "unknown"),
    ("Чабани зниження" + FOOT, "unknown", "Чабани", "descending"),
    ("Іванків уважно" + FOOT, "unknown", "Іванків", "unknown"),
    ("Новий в районі Козин/Українка" + FOOT, "unknown", "Козин", "unknown"),
    ("БПЛА на Наливайлівку" + FOOT, "shahed", "Наливайківка", "moving"),   # typo
    ("Реактивний БпЛА курсом на Ворзель" + FOOT, "jet_uav", "Ворзель", "moving"),
])
def test_report(parser, text, slug, place, status):
    e = first(parser, text)
    assert e is not None, text
    assert (e.threat_type, e.place_name, e.status) == (slug, place, status)


def test_multi_line_reports_are_separate(parser):
    """Each line is its own drone; the first report is not a header that
    becomes every later line's position."""
    evs = parser.parse("Реактивний БпЛА курсом на Трипілля\nРеактивний БпЛА курсом на Димер\n"
                       "Реактивний БпЛА курсом на Чорнобиль" + FOOT, channel=CH)
    assert [e.place_name for e in evs] == ["Трипілля", "Димер", "Чорнобиль"]
    assert all(e.dest_name is None for e in evs)


def test_header_line_is_not_a_sighting(parser):
    evs = parser.parse("Київ:\nРеактивний БпЛА курсом на Вишневе" + FOOT, channel=CH)
    assert len(evs) == 1 and evs[0].threat_type == "jet_uav"


def test_footer_and_promo_lines_are_stripped():
    t = strip_boilerplate("БПЛА на Голосіїв\n\nОнлайн-карта БПЛА - https://t.me/+8npgfltbb1wzMTUy" + FOOT)
    assert t == "БПЛА на Голосіїв"


def test_other_channels_keep_their_rules(parser):
    """The news filter is per channel: kpszsu's aftermath line still parses."""
    assert parser.parse("Реактивний БпЛА курсом на Київ повз Гостомель", channel="kpszsu")
