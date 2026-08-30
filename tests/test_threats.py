"""Threat vocabulary: the many ways the channels name the same thing."""

import pytest

from dronevis.parse.normalize import fold
from dronevis.parse.threats import classify_line

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

MISSILE_CRUISE = [
    "крилата ракета на Львів",
    "калібр з чорного моря",
    "мгКР Бандероль повз Хрестище",
    "Х-101 курсом на Київ",
    "2 бандеролі на Миколаїв",
]

BALLISTIC = [
    "балістика на Київ",
    "загроза балістики",
    "Кинджал у напрямку центру",
    "Іскандер-М по Павлограду",
]

KAB = [
    "КАБ на Запоріжжя",
    "2 КАБа подлетают к Станиславу",
    "пуски керованих авіаційних бомб",
    "УМПБ курсом на Херсон",
]


@pytest.mark.parametrize("text", SHAHED)
def test_shahed_family(text):
    assert classify_line(fold(text)).slug == "shahed"


@pytest.mark.parametrize("text", JET)
def test_jet_uav(text):
    assert classify_line(fold(text)).slug == "jet_uav"


@pytest.mark.parametrize("text", MISSILE_CRUISE)
def test_cruise_missile(text):
    assert classify_line(fold(text)).slug == "cruise_missile"


@pytest.mark.parametrize("text", BALLISTIC)
def test_ballistic(text):
    assert classify_line(fold(text)).slug == "ballistic"


@pytest.mark.parametrize("text", KAB)
def test_kab(text):
    assert classify_line(fold(text)).slug == "kab"


def test_reactive_beats_generic_uav():
    # "реактивний БпЛА" must not fall through to the generic shahed rule
    assert classify_line(fold("реактивний БпЛА над містом")).slug == "jet_uav"


def test_non_threat_returns_none():
    assert classify_line(fold("Дніпро та Полтава чисто, лунали вибухи")) is None
    assert classify_line(fold("Слава Україні!")) is None


def test_uk_ru_fold_equivalence():
    assert classify_line(fold("балістична ціль")).slug == "ballistic"
    assert classify_line(fold("баллистическая цель")).slug == "ballistic"
