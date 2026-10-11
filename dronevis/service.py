"""Glue: fetch -> store raw -> parse -> events -> dedupe -> clusters."""

from __future__ import annotations

import asyncio
import re
import threading
from datetime import datetime, timedelta, timezone

from .areas import area_center
from .config import Config
from .db import Database, utcnow_iso
from .dedupe import Deduper
from .geo.gazetteer import Gazetteer
from .ingest import get_source
from .log import get_logger
from .aviation import active_alerts
from .geo.launch import THREAT_HOLD_MIN
from .parse.normalize import fold, strip_hints
from .parse.pipeline import Parser
from .parse.threats import FAMILY

log = get_logger("service")

# A reply that *adds* drones rather than moving the quoted one (folded text,
# emoji already stripped): "Також реактивні БпЛА …", "Ще один …", "Ще 2 …".
_ADDS_MORE_RX = re.compile(r"\s*(?:також|ще\b|еще\b|и ще|и еще|плюс\b|\+)")


# "Відбій загрози …", "отбой угрозы …" (folded)
_ALL_CLEAR_RX = re.compile(r"\b(?:видбий|отбой)\b")
_BALLISTIC_WORD_RX = re.compile(r"балист|отрк|искандер|кинджал")


def adds_more(text: str) -> bool:
    # hint tokens ("⟦jet⟧" for 🏍) would sit in front of "також"
    return bool(_ADDS_MORE_RX.match(strip_hints(fold(text))))


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
    async def ingest_once(self, wait: bool = False) -> dict:
        """Fetch new posts from every channel, parse + store them.
        Returns ``{channel: new_post_count}``.

        The scheduled poll (``wait=False``) just skips a tick if another writer
        is busy. A user-pressed Fetch (``wait=True``) waits for it instead -
        otherwise, pressing Fetch while the background poll was mid-run
        returned instantly with nothing done, and the UI redrew stale data
        until the next tick (up to a poll interval later)."""
        if wait:
            # blocking acquire off the event loop; a threading.Lock may be
            # released from another thread, so the finally below still works
            await asyncio.to_thread(self._writer.acquire)
        elif not self._writer.acquire(blocking=False):
            log.info("ingest skipped - another ingest or a reparse is running")
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
            if not posts:
                log.warning("no posts at all for channel %r - check the name "
                            "and that the channel's web preview (t.me/s/%s) works",
                            channel, channel)
        else:
            posts = await self.source.fetch_new(channel, latest)

        new = 0
        for post in posts:
            rid, is_new = self.db.upsert_raw_message(
                post.channel, post.msg_id, post.url, post.posted_at_iso, post.text,
                post.reply_to,
            )
            if is_new:
                new += 1
                self._parse_and_store(rid, channel, post.text, post.posted_at_iso,
                                      post.reply_to)
        return new

    async def repair_recent_texts(self, pages: int = 4) -> int:
        """Re-read the last few pages of every channel and correct stored
        posts whose text differs - before 1.0.0 a reply was saved as the
        message it quotes. Run once after an update, before the re-parse.
        Network trouble only skips the repair; it never blocks startup."""
        fixed = 0
        for channel in self.cfg.sources.channels:
            try:
                posts = await self.source.fetch_backfill(channel, pages)
            except Exception as exc:                   # noqa: BLE001
                log.warning("text repair: fetch failed for %s: %s", channel, exc)
                continue
            for post in posts:
                if self.db.update_raw_text(post.channel, post.msg_id, post.text,
                                           post.reply_to):
                    fixed += 1
        if fixed:
            log.info("text repair: corrected %d stored post(s)", fixed)
        return fixed

    # -- parsing --------------------------------------------------------
    def _parse_and_store(
        self, raw_id: int, channel: str, text: str, posted_at: str,
        reply_to: int | None = None,
    ) -> int:
        # A reply's places are resolved near the thread it replies to:
        # "Низько йде, Приморський район" under an Odesa post is Odesa's
        # district, not Приморський in Donetsk oblast.
        quoted = self.db.reply_clusters(channel, reply_to) if reply_to else []
        center = self._area_center
        if quoted:
            q = self.db.query_one("SELECT centroid_lat, centroid_lon FROM cluster WHERE id=?",
                                  (quoted[0]["cluster_id"],))
            if q is not None and q["centroid_lat"] is not None:
                center = (q["centroid_lat"], q["centroid_lon"])
        events = self.parser.parse(text, channel=channel, area_center=center)
        # A reply ("Вінниччина: … Козятина" <- "… через Хмельниччину у напрямку
        # Рівненщини") is the channel saying *that* drone moved on: its report
        # continues the quoted post's track. Not "Також ..." / "Ще один ...",
        # which add more drones.
        follow: list[int] = []
        if quoted and events and not adds_more(text):
            follow = [r["cluster_id"] for r in quoted]
        if not events:
            self._resolve_all_clear(text, channel, posted_at, reply_to)
        # one post -> one transaction (one fsync, and atomic)
        with self.db.transaction():
            for ev in events:
                row = ev.to_row()
                row["raw_message_id"] = raw_id
                row["channel"] = channel
                row["posted_at"] = posted_at
                eid = self.db.insert_event(row)
                try:
                    self.deduper.assign(eid, ev, channel, posted_at, follow=follow)
                except Exception as exc:
                    log.warning("dedupe failed for event %d: %s", eid, exc)
            self.db.mark_parsed(raw_id)
        return len(events)

    def _resolve_all_clear(self, text: str, channel: str, posted_at: str,
                           reply_to: int | None) -> int:
        """A placeless "відбій" ends threats: a reply ("📢 Відбій загрози
        застосування балістичного озброєння." ↩ the threat post) ends exactly
        the quoted one; a plain "⚪️ Відбій загрози балістики." ends the
        ballistic threats of the last hour that haven't come down yet."""
        f = strip_hints(fold(text))
        if not _ALL_CLEAR_RX.search(f):
            return 0
        if reply_to:
            ids = [r["cluster_id"] for r in self.db.reply_clusters(channel, reply_to)]
        elif _BALLISTIC_WORD_RX.search(f):
            since = (datetime.fromisoformat(posted_at) - timedelta(hours=1)).isoformat()
            ids = [r["id"] for r in self.db.query(
                "SELECT id, threat_type FROM cluster WHERE resolved_at IS NULL "
                "AND last_posted_at >= ? AND last_posted_at <= ? "
                "AND status NOT IN ('descending', 'impact')", (since, posted_at))
                if FAMILY.get(r["threat_type"]) == "ballistic"]
        else:
            return 0
        for cid in ids:
            self.db.update_cluster(cid, {"resolved_at": posted_at})
        return len(ids)

    def threat_active(self, now: datetime | None = None) -> bool:
        """Is something going on - poll fast? Any non-"clear" report in the
        last ``quiet_after_minutes``, a missile threat still within its hold
        time (unresolved, not hit), or missile carriers in the air."""
        now = now or datetime.now(timezone.utc)
        quiet = (now - timedelta(minutes=self.cfg.poll.quiet_after_minutes)).isoformat()
        if self.db.query_one(
            "SELECT 1 AS x FROM event WHERE posted_at >= ? AND threat_type <> 'clear' LIMIT 1",
            (quiet,),
        ):
            return True
        held = (now - timedelta(minutes=max(THREAT_HOLD_MIN.values()))).isoformat()
        for r in self.db.query(
            "SELECT threat_type, last_posted_at FROM cluster WHERE resolved_at IS NULL "
            "AND status <> 'impact' AND last_posted_at >= ?", (held,),
        ):
            hold = THREAT_HOLD_MIN.get(FAMILY.get(r["threat_type"]))
            if hold and r["last_posted_at"] >= (now - timedelta(minutes=hold)).isoformat():
                return True
        rows = self.db.query(
            "SELECT channel, url, posted_at, text FROM raw_message WHERE posted_at >= ? "
            "ORDER BY posted_at", ((now - timedelta(hours=12)).isoformat(),))
        posts = [(datetime.fromisoformat(r["posted_at"]), r["channel"], r["url"], r["text"])
                 for r in rows]
        return bool(active_alerts(posts, now))

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
                    r["id"], r["channel"], r["text"], r["posted_at"],
                    r["reply_to"] if "reply_to" in r.keys() else None,
                )
                total_msgs += 1
        return {"messages": total_msgs, "events": total_events}
