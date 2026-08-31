"""Glue: fetch -> store raw -> parse -> events -> dedupe -> clusters."""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta, timezone

from .areas import area_center
from .config import Config
from .db import Database, utcnow_iso
from .dedupe import Deduper
from .geo.gazetteer import Gazetteer
from .ingest import get_source
from .log import get_logger
from .parse.pipeline import Parser

log = get_logger("service")


class Service:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.db = Database(cfg.database_path)
        self.gaz = Gazetteer()
        self.parser = Parser(cfg, self.gaz)
        self.deduper = Deduper(self.db, cfg)
        self.source = get_source(cfg)
        self._area_center = area_center(cfg.default_area)
        # Serialises the two writers - the scheduled poll (on the event loop)
        # and a manual reparse (on a worker thread) - so they never mutate
        # events/clusters at the same time. The poll acquires non-blocking and
        # simply skips a tick if a reparse is in progress; the reparse waits.
        self._writer = threading.Lock()
        self.last_error: str | None = None
        self.last_ingest_stats: dict = {}

    async def aclose(self) -> None:
        await self.source.aclose()
        self.db.close()

    # -- maintenance -----------------------------------------------------
    def prune(self) -> dict:
        """Drop data older than ``retain_days``; VACUUM on the ``vacuum_days``
        cadence. Holds the writer lock (blocks scheduled ingests briefly)."""
        with self._writer:
            cutoff = (datetime.now(timezone.utc)
                      - timedelta(days=self.cfg.retain_days)).isoformat()
            removed = self.db.prune(cutoff)
            last_vac = self.db.get_meta("last_vacuum")
            due = (not last_vac or
                   datetime.now(timezone.utc) - datetime.fromisoformat(last_vac)
                   > timedelta(days=self.cfg.vacuum_days))
            if due and (removed["raw_messages"] or removed["clusters"] or not last_vac):
                self.db.vacuum()
                self.db.set_meta("last_vacuum", utcnow_iso())
            if removed["raw_messages"] or removed["clusters"]:
                log.info("pruned %s (retain %dd)", removed, self.cfg.retain_days)
            return removed

    # -- ingestion --------------------------------------------------------
    async def ingest_once(self) -> dict:
        """Fetch new posts from every channel, parse + store them.
        Returns ``{channel: new_post_count}`` (empty if a reparse is running)."""
        if not self._writer.acquire(blocking=False):
            log.info("ingest skipped - a reparse is in progress")
            return {}
        try:
            stats: dict[str, int] = {}
            for channel in self.cfg.sources.channels:
                try:
                    stats[channel] = await self._ingest_channel(channel)
                except Exception as exc:  # one bad channel must not stop the rest
                    log.warning("ingest failed for %s: %s", channel, exc)
                    stats[channel] = 0
            total = sum(stats.values())
            if total:
                log.info("ingested %d new post(s): %s",
                         total, {k: v for k, v in stats.items() if v})
            self.db.set_meta("last_ingest", utcnow_iso())
            self.last_ingest_stats = stats
            return stats
        except Exception as exc:                       # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            self._writer.release()

    async def _ingest_channel(self, channel: str) -> int:
        latest = self.db.latest_msg_id(channel)
        if latest == 0:
            log.info("first run for %s - backfilling %d page(s)",
                     channel, self.cfg.sources.backfill_pages)
            posts = await self.source.fetch_backfill(
                channel, self.cfg.sources.backfill_pages
            )
        else:
            posts = await self.source.fetch_new(channel, latest)

        new = 0
        for post in posts:
            rid, is_new = self.db.upsert_raw_message(
                post.channel, post.msg_id, post.url, post.posted_at_iso, post.text
            )
            if is_new:
                new += 1
                self._parse_and_store(rid, channel, post.text, post.posted_at_iso)
        return new

    # -- parsing --------------------------------------------------------
    def _parse_and_store(
        self, raw_id: int, channel: str, text: str, posted_at: str
    ) -> int:
        events = self.parser.parse(
            text, channel=channel, area_center=self._area_center
        )
        # one post -> one transaction (one fsync, and atomic)
        with self.db.transaction():
            for ev in events:
                row = ev.to_row()
                row["raw_message_id"] = raw_id
                row["channel"] = channel
                row["posted_at"] = posted_at
                eid = self.db.insert_event(row)
                try:
                    self.deduper.assign(eid, ev, channel, posted_at)
                except Exception as exc:
                    log.warning("dedupe failed for event %d: %s", eid, exc)
            self.db.mark_parsed(raw_id)
        return len(events)

    def reparse_all(self) -> dict:
        """Wipe derived data and re-run the parser over every stored post."""
        with self._writer:                       # block scheduled ingests
            self.db.clear_derived()
            return self._drain_unparsed()

    def reparse_since(self, hours: float) -> dict:
        """Re-run the parser over just the last ``hours`` of posts (plus any
        older post whose cluster is still active in that window). Leaves older
        data alone, so the map never goes briefly empty the way a full
        ``reparse_all`` does."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        with self._writer:
            dropped = self.db.reset_derived_since(cutoff)
            if not dropped["messages"]:
                log.info("reparse_since(%.1fh): nothing in window", hours)
                return {"messages": 0, "events": 0, "clusters_dropped": 0}
            res = self._drain_unparsed()
            log.info("reparse_since(%.1fh): %d posts -> %d events (dropped %d clusters)",
                     hours, res["messages"], res["events"], dropped["clusters"])
            res["clusters_dropped"] = dropped["clusters"]
            return res

    def _drain_unparsed(self) -> dict:
        """Parse every raw_message with parsed_at IS NULL. Caller holds the
        writer lock."""
        total_msgs = total_events = 0
        while True:
            batch = self.db.unparsed_messages(limit=400)
            if not batch:
                break
            for r in batch:
                total_events += self._parse_and_store(
                    r["id"], r["channel"], r["text"], r["posted_at"]
                )
                total_msgs += 1
        return {"messages": total_msgs, "events": total_events}
