"""/api/config and /api/health shape - the web UI depends on these fields."""

import tempfile

import pytest

from dronevis.api import create_app
from dronevis.config import load_config

try:
    from fastapi.testclient import TestClient
except Exception:                                    # pragma: no cover
    TestClient = None

pytestmark = pytest.mark.skipif(TestClient is None, reason="starlette testclient unavailable")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("DRONEVIS_DB_PATH", tempfile.mktemp(suffix=".db"))
    return TestClient(create_app(load_config()))


def test_config_threats_carry_family_and_speed(client):
    body = client.get("/api/config").json()
    assert len(body["threats"]) == 14                 # taxonomy minus the "clear" pseudo-slug
    for th in body["threats"]:
        assert th["slug"] and th["family"] and th["color"]
        assert th["slug"] != "clear"
        assert isinstance(th["speed_kmh"], (int, float))
    fams = {th["family"] for th in body["threats"]}
    assert {"drone", "cruise", "ballistic", "bomb"} <= fams


def test_health_reports_version_and_mqtt(client):
    body = client.get("/api/health").json()
    assert body["status"] in ("ok", "degraded")
    assert body["version"]
    assert set(body["mqtt"]) == {"enabled", "connected"}
    assert "open_clusters" in body["counts"]
    assert body["retain_days"] >= 1
