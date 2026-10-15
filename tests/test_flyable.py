"""Physical plausibility: a track is drawn only through fixes the object could
have flown between, and fades out once nobody reports it."""

from datetime import datetime, timedelta, timezone

from dronevis.predict import plausible_path, threat_life

T0 = datetime(2026, 10, 15, 22, 0, tzinfo=timezone.utc)
SHAHED = 185 * 1.25               # reach_kmh("shahed")

TROIESHCHYNA = (50.51, 30.60)
BROVARY = (50.51, 30.79)          # ~13 km east
BORYSPIL = (50.35, 30.95)         # ~30 km on from Brovary


def _pts(*fixes):
    return [(T0 + timedelta(minutes=m), lat, lon) for m, (lat, lon) in fixes]


def test_steady_flight_keeps_every_fix():
    pts = _pts((0, TROIESHCHYNA), (6, BROVARY), (16, BORYSPIL))
    assert plausible_path(pts, SHAHED) == [0, 1, 2]


def test_a_jump_there_and_back_is_dropped():
    # another channel's Boryspil one minute after Brovary, then Brovary again
    pts = _pts((0, TROIESHCHYNA), (5, BROVARY), (6, BORYSPIL), (8, BROVARY))
    assert 2 not in plausible_path(pts, SHAHED)


def test_the_newest_fix_is_always_drawn():
    pts = _pts((0, TROIESHCHYNA), (1, BORYSPIL))
    assert plausible_path(pts, SHAHED)[-1] == 1


def test_life_holds_then_fades_then_ends():
    assert threat_life("drone", "moving", None) == 1.0
    assert threat_life("drone", "moving", 5) == 1.0
    assert 0 < threat_life("drone", "moving", 18) < 1
    assert threat_life("drone", "moving", 25) == 0.0
    assert threat_life("drone", "circling", 25) > 0
    assert threat_life("cruise", "moving", 60) > 0
    assert threat_life("drone", "impact", 6) == 0.0
