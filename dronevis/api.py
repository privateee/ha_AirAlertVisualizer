"""FastAPI app: JSON API + static web UI + background poller."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import re
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("image/svg+xml", ".svg")

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dateutil import parser as dtp
from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles

from .areas import in_area, resolve_area
from .config import Config, load_config
from .db import Database
from .geo.util import bearing_deg, compass, haversine_km
from .log import get_logger, setup_logging
from .parse.threats import COLOR, LABEL, SHORT
from .service import Service

log = get_logger("api")
WEB_DIR = Path(__file__).parent / "web"

_REL_RE = re.compile(r"^-?(\d+)\s*([smhd])$", re.I)
_UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _parse_time(value: str | None, *, default_hours: float) -> datetime:
    now = datetime.now(timezone.utc)
    if not value:
        return now - timedelta(hours=default_hours)
    m = _REL_RE.match(value.strip())
    if m:
        return now - timedelta(seconds=int(m.group(1)) * _UNIT[m.group(2).lower()])
    try:
        dt = dtp.isoparse(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return now - timedelta(hours=default_hours)


def _csv(value: str | None) -> list[str] | None:
    if not value:
        return None
    items = [v.strip() for v in value.split(",") if v.strip()]
    return items or None


def create_app(cfg: Config | None = None):
    cfg = cfg or load_config()
    setup_logging(cfg.log_level)
    service = Service(cfg)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if cfg.poll.reparse_on_start:
            await asyncio.to_thread(service.reparse_all)
        scheduler = AsyncIOScheduler(timezone="UTC")
        scheduler.add_job(
            service.ingest_once,
            "interval",
            seconds=cfg.poll.interval_seconds,
            next_run_time=datetime.now(timezone.utc),
            max_instances=1,
            coalesce=True,
            id="poll",
        )
        scheduler.start()
        log.info("polling every %ss; UI on http://%s:%d",
                 cfg.poll.interval_seconds, cfg.server.host, cfg.server.port)
        try:
            yield
        finally:
            scheduler.shutdown(wait=False)
            await service.aclose()

    app = FastAPI(title="DroneVisualizer", version="0.1.0", lifespan=lifespan)
    db: Database = service.db

    # -- meta -----------------------------------------------------------
    @app.get("/api/config")
    def api_config():
        return {
            "areas": [
                {
                    "key": a.key, "label": a.label, "center": a.center,
                    "radius_km": a.radius_km, "bbox": a.bbox,
                }
                for a in cfg.areas.defined.values()
            ],
            "default_area": cfg.areas.default,
            "channels": cfg.sources.channels,
            "threats": [
                {"slug": s, "label": LABEL[s], "short": SHORT.get(s, LABEL[s]),
                 "color": COLOR[s]}
                for s in LABEL
            ],
            "tile_url": cfg.server.tile_url,
            "tile_attribution": cfg.server.tile_attribution,
            "tile_url_dark": cfg.server.tile_url_dark,
            "tile_attribution_dark": cfg.server.tile_attribution_dark,
            "map_theme": cfg.server.map_theme,
            "poll_interval": cfg.poll.interval_seconds,
        }

    @app.get("/api/stats")
    def api_stats():
        row = db.query_one(
            "SELECT "
            "(SELECT COUNT(*) FROM raw_message) rm, "
            "(SELECT COUNT(*) FROM event) ev, "
            "(SELECT COUNT(*) FROM cluster) cl"
        )
        return {
            "raw_messages": row["rm"], "events": row["ev"], "clusters": row["cl"],
            "last_ingest": db.get_meta("last_ingest"),
        }

    # -- map data --------------------------------------------------------
    @app.get("/api/clusters")
    def api_clusters(
        area: str | None = None,
        since: str | None = None,
        until: str | None = None,
        threats: str | None = None,
        channels: str | None = None,
        min_conf: float = 0.0,
        located_only: bool = True,
    ):
        t0 = _parse_time(since, default_hours=6)
        t1 = _parse_time(until, default_hours=0) if until else datetime.now(timezone.utc)
        area_obj = resolve_area(cfg, area)
        want_threats = set(_csv(threats) or [])
        want_channels = set(_csv(channels) or [])

        rows = db.query(
            "SELECT * FROM cluster WHERE last_posted_at >= ? AND first_posted_at <= ? "
            "ORDER BY last_posted_at DESC",
            (t0.isoformat(), t1.isoformat()),
        )

        # cheap filters first, so we only pull events for clusters we will show
        kept = []
        for c in rows:
            if want_threats and c["threat_type"] not in want_threats:
                continue
            if located_only and c["centroid_lat"] is None:
                continue
            if area_obj and not in_area(area_obj, c["centroid_lat"], c["centroid_lon"]):
                continue
            if want_channels and not (want_channels & set(_json(c["channels"]))):
                continue
            kept.append(c)

        # one query for every surviving cluster's events (was N+2 per cluster)
        by_cluster: dict[int, list] = defaultdict(list)
        if kept:
            ids = [c["id"] for c in kept]
            ph = ",".join("?" * len(ids))
            for e in db.query(
                f"SELECT e.*, r.url, r.posted_at AS r_posted FROM event e "
                f"JOIN raw_message r ON r.id = e.raw_message_id "
                f"WHERE e.cluster_id IN ({ph}) ORDER BY e.posted_at DESC",
                ids,
            ):
                by_cluster[e["cluster_id"]].append(e)

        out = []
        for c in kept:
            evs = by_cluster.get(c["id"], [])
            if min_conf and max((e["parse_confidence"] for e in evs), default=0.0) < min_conf:
                continue
            out.append(_cluster_dto(c, evs))
        return {"count": len(out), "clusters": out}

    @app.get("/api/messages")
    def api_messages(
        channels: str | None = None,
        since: str | None = None,
        until: str | None = None,
        q: str | None = None,
        limit: int = Query(200, le=1000),
    ):
        t0 = _parse_time(since, default_hours=12)
        params: list = [t0.isoformat()]
        sql = "SELECT * FROM raw_message WHERE posted_at >= ?"
        if until:
            sql += " AND posted_at <= ?"
            params.append(_parse_time(until, default_hours=0).isoformat())
        want_channels = _csv(channels)
        if want_channels:
            sql += f" AND channel IN ({','.join('?' * len(want_channels))})"
            params.extend(want_channels)
        if q:
            sql += " AND text LIKE ?"
            params.append(f"%{q}%")
        sql += " ORDER BY posted_at DESC LIMIT ?"
        params.append(limit)

        rows = db.query(sql, params)

        ev_by_msg: dict[int, list] = defaultdict(list)
        if rows:
            ids = [r["id"] for r in rows]
            ph = ",".join("?" * len(ids))
            for e in db.query(
                f"SELECT raw_message_id, threat_type, status, place_name, dest_name, "
                f"heading_deg, count, lat, lon FROM event WHERE raw_message_id IN ({ph}) "
                f"ORDER BY id",
                ids,
            ):
                ev_by_msg[e["raw_message_id"]].append(e)

        msgs = [
            {
                "id": r["id"], "channel": r["channel"], "msg_id": r["msg_id"],
                "url": r["url"], "posted_at": r["posted_at"], "text": r["text"],
                "events": [
                    {
                        "threat_type": e["threat_type"], "status": e["status"],
                        "place_name": e["place_name"], "dest_name": e["dest_name"],
                        "heading": compass(e["heading_deg"]), "count": e["count"],
                        "lat": e["lat"], "lon": e["lon"],
                        "color": COLOR.get(e["threat_type"], "#888"),
                    }
                    for e in ev_by_msg.get(r["id"], [])
                ],
            }
            for r in rows
        ]
        return {"count": len(msgs), "messages": msgs}

    # -- actions --------------------------------------------------------
    @app.post("/api/ingest")
    async def api_ingest():
        return {"ingested": await service.ingest_once()}

    @app.post("/api/reparse")
    async def api_reparse():
        return await asyncio.to_thread(service.reparse_all)

    if WEB_DIR.exists():
        app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")

    # Home Assistant ingress forwards request paths with a doubled leading
    # slash ("//api/config"), which the router won't match. Collapse repeated
    # slashes before anything else sees the request. No-op outside ingress.
    return _CollapseSlashes(app)


class _CollapseSlashes:
    _RE = re.compile(r"/{2,}")
    _REB = re.compile(rb"/{2,}")

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and "//" in scope.get("path", ""):
            scope = dict(scope)
            scope["path"] = self._RE.sub("/", scope["path"])
            raw = scope.get("raw_path")
            if raw:
                head, sep, tail = raw.partition(b"?")
                scope["raw_path"] = self._REB.sub(b"/", head) + sep + tail
        await self.app(scope, receive, send)


def _json(text: str):
    try:
        return json.loads(text)
    except Exception:
        return []


def _cluster_dto(c, evs) -> dict:
    """``evs`` is this cluster's events, newest first."""
    last = c["last_posted_at"]
    age_min = None
    try:
        age_min = round(
            (datetime.now(timezone.utc) - dtp.isoparse(last)).total_seconds() / 60, 1
        )
    except Exception:
        pass

    # movement guess: successive reported positions, oldest -> newest,
    # consecutive duplicates collapsed
    track: list[list[float]] = []
    for e in reversed(evs):
        if e["lat"] is None:
            continue
        pt = [round(e["lat"], 4), round(e["lon"], 4)]
        if not track or track[-1] != pt:
            track.append(pt)

    # heading from the last observed leg beats the parsed one when we have
    # two genuinely separate positions
    heading = c["heading_deg"]
    observed = False
    if len(track) >= 2:
        a, b = track[-2], track[-1]
        if haversine_km((a[0], a[1]), (b[0], b[1])) >= 4:
            heading = bearing_deg((a[0], a[1]), (b[0], b[1]))
            observed = True

    return {
        "id": c["id"],
        "threat_type": c["threat_type"],
        "threat_label": LABEL.get(c["threat_type"], c["threat_type"]),
        "color": COLOR.get(c["threat_type"], "#888"),
        "status": c["status"],
        "first_posted_at": c["first_posted_at"],
        "last_posted_at": last,
        "age_minutes": age_min,
        "lat": c["centroid_lat"],
        "lon": c["centroid_lon"],
        "place_name": c["place_name"],
        "dest_name": c["dest_name"],
        "dest_lat": c["dest_lat"],
        "dest_lon": c["dest_lon"],
        "heading_deg": heading,
        "compass": compass(heading),
        "heading_observed": observed,
        "track": track,
        "count": c["count"],
        "count_max": c["count_max"],
        "event_count": c["event_count"],
        "channels": _json(c["channels"]),
        "sources": [
            {
                "channel": e["channel"], "url": e["url"],
                "posted_at": e["r_posted"], "line": e["raw_line"],
                "count": e["count"], "status": e["status"],
                "confidence": e["parse_confidence"],
            }
            for e in evs[:8]
        ],
    }


# uvicorn entrypoint: ``uvicorn dronevis.api:app``
app = create_app()
