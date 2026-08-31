"""Threat vocabulary: the many ways the channels name the same thing."""

import pytest

from dronevis.parse.normalize import fold
from dronevis.parse.threats import FAMILY, classify_line


def slug(text):
    m = classify_line(fold(text))
    return m.slug if m else None


SHAHED = [
    "шахед на місто",
    "шахеди курсом на Київ",
    "5 шахедів з півдня",
    "shahed group",
    "мопед над Броварами",
    "мопеди пішли на Обухів",
    "герань-2 у напрямку Фастова",
    "дрон-камікадзе на Васильків",
    "дрони над Києвом",
    "БпЛА курсом на Бориспіль",
    "безпілотник на Ірпінь",
    "1 нереактивний на місто",
]

JET = [
    "реактив довкола Кропивницького",
    "реактиви від Оржиці",
    "3х реактиви на Славутич",
    "реактивний БпЛА повз Велику Димерку",
    "реактивна ціль на Київ",
    "реактів на Обухів",
    "реактівний мопед курсом на Вишгород",
    "управляемый реактивный мопед подлетает к Николаеву",
]

# text -> expected exact slug (all are family "cruise")
CRUISE = {
    "крилата ракета на Львів": "cruise_missile",
    "калібр з чорного моря": "kalibr",
    "мгКР Бандероль повз Хрестище": "banderol",
    "Х-101 курсом на Київ": "x101",
    "2 бандеролі на Миколаїв": "banderol",
    "Х-22 по Одесі": "x22",
    "Іскандер-К на Київ": "cruise_missile",
}

# text -> expected exact slug (all are family "ballistic")
BALLISTIC = {
    "балістика на Київ": "ballistic",
    "загроза балістики": "ballistic",
    "Кинжал у напрямку центру": "kinzhal",
    "Іскандер-М по Павлограду": "iskander",
    "2 іскандери на Харків": "iskander",
    "КН-23 по Дніпру": "iskander",
}

KAB = [
    "КАБ на Запоріжжя",
    "2 КАБа подлетают к Станиславу",
    "пуски керованих авіаційних бомб",
    "УМПБ курсом на Херсон",
]


@pytest.mark.parametrize("text", SHAHED)
def test_shahed(text):
    assert slug(text) == "shahed"


@pytest.mark.parametrize("text", JET)
def test_jet_uav(text):
    assert slug(text) == "jet_uav"


@pytest.mark.parametrize("text,want", CRUISE.items())
def test_cruise_family(text, want):
    s = slug(text)
    assert FAMILY.get(s) == "cruise", f"{text!r} -> {s}"
    assert s == want


@pytest.mark.parametrize("text,want", BALLISTIC.items())
def test_ballistic_family(text, want):
    s = slug(text)
    assert FAMILY.get(s) == "ballistic", f"{text!r} -> {s}"
    assert s == want


@pytest.mark.parametrize("text", KAB)
def test_kab(text):
    assert slug(text) == "kab"


def test_reactive_beats_generic_uav():
    assert slug("реактивний БпЛА над містом") == "jet_uav"


def test_specific_beats_generic_within_family():
    # a named sub-type must win over the generic family rule
    assert slug("крилата ракета Калібр на Одесу") == "kalibr"
    assert slug("балістика: Кинжал по аеродрому") == "kinzhal"


def test_non_threat_returns_none():
    assert classify_line(fold("Дніпро та Полтава чисто, лунали вибухи")) is None
    assert classify_line(fold("Слава Україні!")) is None


def test_uk_ru_fold_equivalence():
    assert slug("балістична ціль") == "ballistic"
    assert slug("баллистическая цель") == "ballistic"
