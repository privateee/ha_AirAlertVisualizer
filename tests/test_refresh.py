"""The "Fetch / Refresh doesn't update the map" fixes on the server side:
a user-pressed ingest waits for a background poll already in progress
(instead of silently doing nothing), and API responses are never cacheable."""

import asyncio
import tempfile

from dronevis.config import load_config
from dronevis.service import Service


def _service(tmp_path, monkeypatch) -> Service:
    monkeypatch.setenv("DRONEVIS_DB_PATH", str(tmp_path / "s.db"))
    svc = Service(load_config())

    async def fake_channel(channel):          # no network in tests
        return 1

    svc._ingest_channel = fake_channel
    return svc


def test_manual_ingest_waits_for_a_running_poll(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch)
    svc._writer.acquire()                     # a background poll is mid-run

    async def main():
        task = asyncio.create_task(svc.ingest_once(wait=True))
        await asyncio.sleep(0.2)
        assert not task.done()                # waiting for it, not skipped
        svc._writer.release()                 # the poll finishes
        return await asyncio.wait_for(task, 5)

    try:
        stats = asyncio.run(main())
        assert sum(stats.values()) == len(svc.cfg.sources.channels)
    finally:
        asyncio.run(svc.aclose())


def test_scheduled_poll_still_skips_when_busy(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch)
    svc._writer.acquire()
    try:
        assert asyncio.run(svc.ingest_once()) == {}
    finally:
        svc._writer.release()
        asyncio.run(svc.aclose())


def test_api_responses_are_not_cacheable(monkeypatch):
    from fastapi.testclient import TestClient

    from dronevis.api import create_app

    monkeypatch.setenv("DRONEVIS_DB_PATH", tempfile.mktemp(suffix=".db"))
    client = TestClient(create_app(load_config()))
    for path in ("/api/clusters", "/api/messages", "/api/config"):
        r = client.get(path)
        assert r.status_code == 200
        assert r.headers.get("cache-control") == "no-store", path
    # the page's own files are revalidated, so an update reaches phones
    for path in ("/", "/app.js", "/style.css"):
        r = client.get(path)
        assert r.status_code == 200
        assert r.headers.get("cache-control") == "no-cache", path
