"""SQLite persistence layer.

Plain ``sqlite3`` on purpose: no ORM, no build step, trivially portable to a
Raspberry Pi. Three tables:

    raw_message  one Telegram post, verbatim
    event        one structured observation extracted from a post
                 (a post can yield several events - one per line/threat)
    cluster      a group of events describing the same object across channels
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_message (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    channel     TEXT    NOT NULL,
    msg_id      INTEGER NOT NULL,
    url         TEXT    NOT NULL,
    posted_at   TEXT    NOT NULL,          -- ISO-8601 UTC
    text        TEXT    NOT NULL,
    fetched_at  TEXT    NOT NULL,
    parsed_at   TEXT,
    UNIQUE (channel, msg_id)
);
CREATE INDEX IF NOT EXISTS ix_raw_posted   ON raw_message (posted_at);
CREATE INDEX IF NOT EXISTS ix_raw_channel  ON raw_message (channel, posted_at);
CREATE INDEX IF NOT EXISTS ix_raw_unparsed ON raw_message (parsed_at);

CREATE TABLE IF NOT EXISTS event (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_message_id  INTEGER NOT NULL REFERENCES raw_message(id) ON DELETE CASCADE,
    channel         TEXT    NOT NULL,
    posted_at       TEXT    NOT NULL,
    threat_type     TEXT    NOT NULL,      -- slug, see parse/threats.py
    threat_raw      TEXT,
    count           INTEGER,
    status          TEXT    NOT NULL DEFAULT 'unknown',
    place_name      TEXT,
    lat             REAL,
    lon             REAL,
    geo_confidence  REAL    NOT NULL DEFAULT 0,
    src_name        TEXT,
    src_lat         REAL,
    src_lon         REAL,
    dest_name       TEXT,
    dest_lat        REAL,
    dest_lon        REAL,
    heading_deg     REAL,
    raw_line        TEXT,
    parse_method    TEXT    NOT NULL DEFAULT 'rules',
    parse_confidence REAL   NOT NULL DEFAULT 0,
    cluster_id      INTEGER REFERENCES cluster(id) ON DELETE SET NULL,
    created_at      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_event_posted  ON event (posted_at);
CREATE INDEX IF NOT EXISTS ix_event_threat  ON event (threat_type, posted_at);
-- covering: cluster centroid recompute + track fetch read the index only
DROP INDEX IF EXISTS ix_event_cluster;
CREATE INDEX IF NOT EXISTS ix_event_cluster_cov ON event (cluster_id, posted_at, lat, lon);

CREATE TABLE IF NOT EXISTS cluster (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    threat_type      TEXT    NOT NULL,
    status           TEXT    NOT NULL DEFAULT 'unknown',
    first_posted_at  TEXT    NOT NULL,
    last_posted_at   TEXT    NOT NULL,
    centroid_lat     REAL,
    centroid_lon     REAL,
    place_name       TEXT,
    dest_name        TEXT,
    dest_lat         REAL,
    dest_lon         REAL,
    heading_deg      REAL,
    count            INTEGER,                        -- drones in the group (latest report)
    count_max        INTEGER,                        -- peak reported
    event_count      INTEGER NOT NULL DEFAULT 0,     -- number of reports
    channels         TEXT    NOT NULL DEFAULT '[]',  -- json list
    resolved_at      TEXT,                           -- set when an "all clear" landed
    updated_at       TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_cluster_last   ON cluster (last_posted_at);
CREATE INDEX IF NOT EXISTS ix_cluster_threat ON cluster (threat_type, last_posted_at);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Thread-checked SQLite wrapper. One connection, guarded by a lock so the
    APScheduler worker thread and the API event loop can share it safely."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self.path, check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Additive column migrations for databases created by older versions."""
        have = {r["name"] for r in self._conn.execute("PRAGMA table_info(cluster)")}
        for col, decl in (
            ("count", "INTEGER"), ("count_max", "INTEGER"), ("resolved_at", "TEXT"),
        ):
            if col not in have:
                self._conn.execute(f"ALTER TABLE cluster ADD COLUMN {col} {decl}")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Group writes into one commit (one fsync instead of many). Holds the
        connection lock for the whole block, so keep the body short. Not
        re-entrant - do not nest."""
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    # -- low level -----------------------------------------------------------
    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, tuple(params))

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # -- meta -------------------------------------------------------------
    def get_meta(self, key: str) -> str | None:
        row = self.query_one("SELECT value FROM meta WHERE key=?", (key,))
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    # -- raw_message ----------------------------------------------------------
    def upsert_raw_message(
        self, channel: str, msg_id: int, url: str, posted_at: str, text: str
    ) -> tuple[int, bool]:
        """Insert a post if new. Returns (row_id, is_new)."""
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO raw_message(channel, msg_id, url, posted_at, text, fetched_at) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(channel, msg_id) DO NOTHING",
                (channel, msg_id, url, posted_at, text, utcnow_iso()),
            )
            if cur.rowcount:
                return int(cur.lastrowid), True
            row = self._conn.execute(
                "SELECT id FROM raw_message WHERE channel=? AND msg_id=?",
                (channel, msg_id),
            ).fetchone()
            return int(row["id"]), False

    def latest_msg_id(self, channel: str) -> int:
        row = self.query_one(
            "SELECT MAX(msg_id) AS m FROM raw_message WHERE channel=?", (channel,)
        )
        return int(row["m"]) if row and row["m"] is not None else 0

    def unparsed_messages(self, limit: int = 500) -> list[sqlite3.Row]:
        return self.query(
            "SELECT * FROM raw_message WHERE parsed_at IS NULL ORDER BY posted_at LIMIT ?",
            (limit,),
        )

    def mark_parsed(self, raw_message_id: int) -> None:
        self.execute(
            "UPDATE raw_message SET parsed_at=? WHERE id=?",
            (utcnow_iso(), raw_message_id),
        )

    def clear_derived(self) -> None:
        """Drop all events + clusters and mark every raw message unparsed.
        Used by ``reparse``."""
        with self._lock:
            self._conn.execute("DELETE FROM event")
            self._conn.execute("DELETE FROM cluster")
            self._conn.execute("UPDATE raw_message SET parsed_at=NULL")

    # -- event --------------------------------------------------------------
    def insert_event(self, e: dict[str, Any]) -> int:
        cols = (
            "raw_message_id", "channel", "posted_at", "threat_type", "threat_raw",
            "count", "status", "place_name", "lat", "lon", "geo_confidence",
            "src_name", "src_lat", "src_lon", "dest_name", "dest_lat", "dest_lon",
            "heading_deg", "raw_line", "parse_method", "parse_confidence",
        )
        values = [e.get(c) for c in cols]
        placeholders = ",".join("?" * (len(cols) + 1))
        with self._lock:
            cur = self._conn.execute(
                f"INSERT INTO event({','.join(cols)}, created_at) VALUES({placeholders})",
                (*values, utcnow_iso()),
            )
            return int(cur.lastrowid)

    def set_event_cluster(self, event_id: int, cluster_id: int) -> None:
        self.execute("UPDATE event SET cluster_id=? WHERE id=?", (cluster_id, event_id))

    # -- cluster ----------------------------------------------------------
    def open_clusters(self, since_iso: str) -> list[sqlite3.Row]:
        return self.query(
            "SELECT * FROM cluster WHERE last_posted_at >= ? ORDER BY last_posted_at DESC",
            (since_iso,),
        )

    def insert_cluster(self, c: dict[str, Any]) -> int:
        cols = (
            "threat_type", "status", "first_posted_at", "last_posted_at",
            "centroid_lat", "centroid_lon", "place_name", "dest_name",
            "dest_lat", "dest_lon", "heading_deg", "count", "count_max",
            "event_count", "channels",
        )
        values = [c.get(x) for x in cols]
        placeholders = ",".join("?" * (len(cols) + 1))
        with self._lock:
            cur = self._conn.execute(
                f"INSERT INTO cluster({','.join(cols)}, updated_at) VALUES({placeholders})",
                (*values, utcnow_iso()),
            )
            return int(cur.lastrowid)

    def update_cluster(self, cluster_id: int, c: dict[str, Any]) -> None:
        fields = ", ".join(f"{k}=?" for k in c)
        with self._lock:
            self._conn.execute(
                f"UPDATE cluster SET {fields}, updated_at=? WHERE id=?",
                (*c.values(), utcnow_iso(), cluster_id),
            )
