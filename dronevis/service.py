"""Glue: fetch -> store raw -> parse -> events -> dedupe -> clusters."""

from __future__ import annotations

import asyncio
import threading

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

    async def aclose(self) -> None:
        await self.source.aclose()
        self.db.close()

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
            return stats
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
            log.info("reparsed %d posts -> %d events", total_msgs, total_events)
            return {"messages": total_msgs, "events": total_events}
