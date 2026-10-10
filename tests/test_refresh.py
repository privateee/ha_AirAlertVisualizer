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


def test_page_links_versioned_assets(monkeypatch):
    """A new add-on version changes the js/css URLs, so a WebView that cached
    the old app.js can't keep running it."""
    from fastapi.testclient import TestClient

    from dronevis import __version__
    from dronevis.api import create_app

    monkeypatch.setenv("DRONEVIS_DB_PATH", tempfile.mktemp(suffix=".db"))
    client = TestClient(create_app(load_config()))
    for path in ("/", "/index.html"):
        html = client.get(path).text
        assert f'src="app.js?v={__version__}"' in html, path
        assert f'href="style.css?v={__version__}"' in html, path
    assert client.get("/api/config").json()["version"] == __version__
    assert client.get(f"/app.js?v={__version__}").status_code == 200


def test_new_version_reparses_recent_posts_once(monkeypatch):
    """After an upgrade the stored recent posts are re-read with the new
    parser once, so parser fixes reach the existing map."""
    from fastapi.testclient import TestClient

    from dronevis.api import create_app
    from dronevis.service import Service

    calls = []
    monkeypatch.setattr(Service, "reparse_since",
                        lambda self, hours: calls.append(hours) or {})
    monkeypatch.setenv("DRONEVIS_DB_PATH", tempfile.mktemp(suffix=".db"))
    cfg = load_config()
    cfg.poll.reparse_on_start = False
    monkeypatch.setattr("dronevis.service.Service.ingest_once",
                        lambda self, wait=False: _noop())
    repairs = []
    monkeypatch.setattr("dronevis.service.Service.repair_recent_texts",
                        lambda self, pages=4: repairs.append(pages) or _noop())

    for _ in range(2):                         # second start: same version
        with TestClient(create_app(cfg)) as client:
            assert client.get("/api/config").status_code == 200
    assert calls == [48]
    assert len(repairs) == 1                   # stored texts repaired once, too


async def _noop():
    return {}


def test_one_bad_channel_does_not_break_the_others(tmp_path, monkeypatch):
    """A wrong channel address (or a channel that errors / returns nothing)
    must not stop ingestion, the startup text repair, or the poll for the rest."""
    from datetime import datetime, timezone

    from dronevis.ingest.base import RawPost

    monkeypatch.setenv("DRONEVIS_DB_PATH", str(tmp_path / "b.db"))
    monkeypatch.setenv("DRONEVIS_CHANNELS", "broken,t.me/nonexistent_xyz,kpszsu")
    svc = Service(load_config())
    assert svc.cfg.sources.channels == ["broken", "nonexistent_xyz", "kpszsu"]

    class FakeSource:
        async def fetch_backfill(self, channel, pages):
            if channel == "broken":
                raise RuntimeError("boom")          # e.g. a malformed page
            if channel == "nonexistent_xyz":
                return []                            # wrong address: empty page
            return [RawPost(channel, 1, "https://t.me/kpszsu/1",
                            datetime(2026, 10, 11, tzinfo=timezone.utc),
                            "🏍 Реактивний БпЛА курсом на Київ повз Гостомель")]

        async def fetch_new(self, channel, after_id):
            return await self.fetch_backfill(channel, 1)

        async def aclose(self):
            pass

    svc.source = FakeSource()
    try:
        stats = asyncio.run(svc.ingest_once(wait=True))
        assert stats == {"broken": 0, "nonexistent_xyz": 0, "kpszsu": 1}
        assert svc.db.query_one("SELECT COUNT(*) n FROM event")["n"] >= 1
        assert asyncio.run(svc.repair_recent_texts(pages=1)) == 0   # no crash either
        assert asyncio.run(svc.ingest_once()) == {"broken": 0, "nonexistent_xyz": 0, "kpszsu": 0}
    finally:
        asyncio.run(svc.aclose())
