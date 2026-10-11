"""Missile-carrier aviation as a timer; relocations are not threats."""

from datetime import datetime, timedelta, timezone

import pytest

from dronevis.aviation import active_alerts, classify

T = datetime(2026, 10, 11, 1, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("text,expect", [
    ("✈️ Зліт МіГ-31К з аеродрому Саваслейка! Загроза застосування Кинджалів.", [("mig31", "up")]),
    ("Ту-95МС в повітрі, можливі пуски крилатих ракет", [("strategic", "up")]),
    ("На південно-східному напрямку присутній борт Ту-22м3 в повітрі за попередніми даними.", [("tu22", "up")]),
    ("в воздухе 3 борта ТУ-160", [("strategic", "up")]),
    ("Ан-160 злетіли", [("strategic", "up")]),
    ("Перебазування Ту-95МС з Енгельса на Оленью", [("strategic", "reloc")]),
    ("Ту-22М3 перелетіли на аеродром Шайковка", [("tu22", "reloc")]),
    ("МіГ-31К приземлився", [("mig31", "down")]),
    ("📢 Відбій загрози застосування Кинджалів", [("mig31", "down")]),
    ("Повітряні сили ЗСУ: Ту-95 в повітрі", [("strategic", "up")]),     # "сили" isn't a landing
    ("Активність тактичної авіації на півночі", []),                     # tactical: not a timer
    ("Реактивний БпЛА курсом на Київ", []),
])
def test_classify(text, expect):
    assert [(m.group, m.kind) for m in classify(text)] == expect


def _p(mins, text, ch="kpszsu"):
    return (T + timedelta(minutes=mins), ch, f"u{mins}", text)


def test_takeoff_runs_a_timer_until_landing():
    posts = [_p(0, "Зліт МіГ-31К!"), _p(5, "МіГ-31К в повітрі", ch="AerisRimor")]
    (a,) = active_alerts(posts, T + timedelta(minutes=12))
    assert a["group"] == "mig31" and a["minutes"] == 12 and a["launch_window_min"] == [15, 40]
    assert a["channels"] == ["AerisRimor", "kpszsu"]
    assert active_alerts(posts + [_p(20, "МіГ-31К приземлився")], T + timedelta(minutes=25)) == []


def test_relocation_is_never_an_alert():
    posts = [_p(0, "Перебазування Ту-95МС з Енгельса на Оленью")]
    assert active_alerts(posts, T + timedelta(minutes=30)) == []


def test_alert_times_out():
    posts = [_p(0, "Зліт МіГ-31К!")]
    assert active_alerts(posts, T + timedelta(minutes=60))
    assert active_alerts(posts, T + timedelta(minutes=130)) == []


def test_timer_stays_until_cancelled_or_called_a_relocation():
    up = [_p(0, "Ту-95МС в повітрі")]
    assert active_alerts(up, T + timedelta(hours=6))                        # all evening
    assert active_alerts(up + [_p(30, "Ту-95МС: це перебазування на Оленью")],
                         T + timedelta(minutes=40)) == []
    assert active_alerts(up + [_p(30, "Зліт Ту-95 не підтверджується")],
                         T + timedelta(minutes=40)) == []
    assert active_alerts(up + [_p(30, "Інформацію про Ту-95 скасовано")],
                         T + timedelta(minutes=40)) == []
