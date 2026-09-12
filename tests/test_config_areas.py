"""The built-in region list: every oblast + Kyiv/Dnipro/All Ukraine, so the
Home Assistant add-on - which ships no config.yaml/config.example.yaml at
all and would otherwise only ever offer "All Ukraine" - gets the full
picker too. Also covers the "custom area" env-var injection that keeps the
add-on's own single-area option working without clobbering the presets."""

import os

import pytest

from dronevis.config import load_config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    # isolate from any DRONEVIS_* the real environment might have set
    for k in list(os.environ):
        if k.startswith("DRONEVIS_"):
            monkeypatch.delenv(k, raising=False)
    # force the "no config file found" branch, same as the add-on container
    monkeypatch.setenv("DRONEVIS_CONFIG", "definitely-does-not-exist.yaml")


def test_no_config_file_still_gets_the_full_region_list():
    cfg = load_config()
    assert len(cfg.areas.defined) == 25
    assert cfg.areas.default == "kyiv"
    assert cfg.areas.defined["kyiv"].label == "Kyiv + oblast"
    assert cfg.areas.defined["ukraine"].label == "All Ukraine"


def test_every_oblast_area_has_a_nearby_reach():
    cfg = load_config()
    for key, area in cfg.areas.defined.items():
        if key in ("kyiv", "dnipro", "ukraine"):
            continue
        assert area.center is not None
        assert area.radius_km == 180
        assert area.label.endswith("+ nearby")


def test_addon_default_area_options_do_not_duplicate_kyiv(monkeypatch):
    # the add-on's run script always exports these, even when the user has
    # not touched the option - which is exactly "Kyiv + oblast" by default
    monkeypatch.setenv("DRONEVIS_AREA_LABEL", "Kyiv + oblast")
    monkeypatch.setenv("DRONEVIS_AREA_CENTER", "50.4501,30.5234")
    monkeypatch.setenv("DRONEVIS_AREA_RADIUS_KM", "130")
    cfg = load_config()
    assert len(cfg.areas.defined) == 25          # no extra "custom" entry
    assert cfg.areas.default == "kyiv"
    assert cfg.areas.defined["kyiv"].label == "Kyiv + oblast"


def test_addon_custom_area_is_added_alongside_the_presets(monkeypatch):
    monkeypatch.setenv("DRONEVIS_AREA_LABEL", "My Town")
    monkeypatch.setenv("DRONEVIS_AREA_CENTER", "49.0,32.0")
    monkeypatch.setenv("DRONEVIS_AREA_RADIUS_KM", "75")
    cfg = load_config()
    assert len(cfg.areas.defined) == 26          # 25 presets + custom
    assert cfg.areas.default == "custom"
    assert cfg.areas.defined["custom"].label == "My Town"
    assert cfg.areas.defined["custom"].center == (49.0, 32.0)
    assert cfg.areas.defined["custom"].radius_km == 75.0
    # the presets are untouched, not overwritten
    assert cfg.areas.defined["kyiv"].label == "Kyiv + oblast"
    assert cfg.areas.defined["kyiv"].center == (50.4501, 30.5234)


def test_explicit_areas_config_is_unaffected(monkeypatch):
    """A real config.yaml/config.example.yaml with its own areas.defined
    still works exactly as written - the built-in list is a fallback only
    for when no areas are configured at all."""
    monkeypatch.delenv("DRONEVIS_CONFIG", raising=False)
    cfg = load_config("config.example.yaml")
    assert len(cfg.areas.defined) == 25
    assert cfg.areas.default == "kyiv"
