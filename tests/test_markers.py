"""Channel marker emoji, target altitude and "зниження" (descending)."""

import pytest

from dronevis.config import load_config
from dronevis.parse.pipeline import Parser, extract_altitude
from dronevis.parse.normalize import fold


@pytest.fixture(scope="module")
def parser():
    return Parser(load_config())


@pytest.mark.parametrize("channel,text,slug,place", [
    ("kpszsu", "🏍 На Мінський масив!", "jet_uav", "Мінський масив"),
    ("kpszsu", "🏍 Воскресенка!", "jet_uav", "Воскресенка"),
    ("kpszsu", "🛵 БпЛА на Харків із півночі", "shahed", "Харків"),
    ("war_monitor", "Київ:\n🅿️1х Троєщина", "shahed", "Троєщина"),
    ("war_monitor", "🎮1х Совки 1,8км", "shahed", "Совки"),
    ("war_monitor", "🔻Зниження Либідська", "shahed", "Либідська"),
])
def test_emoji_is_the_threat_class(parser, channel, text, slug, place):
    evs = parser.parse(text, channel=channel)
    assert evs and evs[0].threat_type == slug and evs[0].place_name == place
    assert "⟦" not in evs[0].raw_line          # hint tokens never reach the UI


def test_circling_marker(parser):
    ev = parser.parse("🔄2х сектор Васильків/ Глеваха", channel="war_monitor")[0]
    assert ev.status == "circling" and ev.count == 2


@pytest.mark.parametrize("text,status", [
    ("🔻Зниження Деміївка", "descending"),
    ("Київ:\n🅿️1х Голосіїв зниження", "descending"),
    ("Київ:\n🅿️1х Теремки 3,2км", "unknown"),
])
def test_descending(parser, text, status):
    assert parser.parse(text, channel="war_monitor")[0].status == status


@pytest.mark.parametrize("line,alt", [
    ("1х Чайки 400м", 400),
    ("1х Теремки 3,2км", 3200),
    ("1х Воскресенка 1км", 1000),
    ("БпЛА на висоті 300 м над Обуховом", 300),
    ("шахеди за 10 км від Києва", None),        # a distance, not a height
    ("БпЛА в 5 км на північ", None),
    ("1х Троєщина", None),
])
def test_altitude(line, alt):
    assert extract_altitude(fold(line)) == alt
