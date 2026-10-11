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


def test_channel_names_accept_links_and_handles(monkeypatch):
    """"t.me/kievreal1" in the add-on options fetched t.me/s/t.me/kievreal1 -
    an empty page, silently: the channel never showed up."""
    from dronevis.config import channel_names, load_config

    assert channel_names(["t.me/kievreal1", "@kpszsu", "https://t.me/s/war_monitor/47552",
                          " AerisRimor ", "kievreal1", "https://telegram.me/x?y=1"]) == \
        ["kievreal1", "kpszsu", "war_monitor", "AerisRimor", "x"]
    monkeypatch.setenv("DRONEVIS_CHANNELS", "t.me/kievreal1,@kpszsu")
    cfg = load_config()
    assert cfg.sources.channels == ["kievreal1", "kpszsu"]
    assert "kievreal1" in cfg.parse.news_channels       # so the news filter applies


def test_outage_news_is_not_a_sighting(parser):
    """kievreal1, 2026-10-11 09:53: emergency power cuts, not a drone over Kyiv."""
    assert parser.parse("В КЦ повідомили, що в Києві ввели екстрені" + FOOT, channel=CH) == []


@pytest.mark.parametrize("text", [
    "Напруга зараз місцями по Києву та області",       # 2026-10-11 11:22, power news
    "Київ",                                              # a bare big city says nothing
    "По Києву та області",
])
def test_bare_big_city_is_not_a_sighting(parser, text):
    assert parser.parse(text + FOOT, channel=CH) == []


def test_kyiv_banks_are_in_kyiv(parser):
    """"Правий берег" used to resolve to a district of Mariupol (GeoNames) and
    "Лівий берег" to one near Dnipro - kievreal1 means Kyiv's banks."""
    for text in ("Правий берег", "Лівий берег БПЛА"):
        (e,) = parser.parse(text + FOOT, channel=CH, area_center=(50.45, 30.52))
        assert e.place_name.endswith("(Київ)") and 50.3 < e.lat < 50.6 and 30.3 < e.lon < 30.8
