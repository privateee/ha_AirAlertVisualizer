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
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .areas import area_center, cluster_touches_area, in_area, resolve_area
from .aviation import active_alerts
from .config import Config, load_config
from .db import Database
from .geo.launch import (BALLISTIC_AVG_KMH, HYPERSONIC_AVG_KMH, THREAT_HOLD_MIN,
                          candidates, reaches, site_at)
from .geo.util import compass, haversine_km
from .ha import HAPublisher, compute_state
from .log import get_logger, setup_logging
from .parse.threats import ALL_SLUGS, COLOR, FAMILY, LABEL, SHORT, SPEED_KMH
from .predict import assess, cluster_motion, cone, dead_reckon
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


def _latlon(value: str | None) -> tuple[float, float] | None:
    """``"50.45,30.52"`` -> (50.45, 30.52); anything else -> None."""
    try:
        lat, lon = (float(x) for x in (value or "").split(","))
    except ValueError:
        return None
    return (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 else None


def _csv(value: str | None) -> list[str] | None:
    if not value:
        return None
    items = [v.strip() for v in value.split(",") if v.strip()]
    return items or None


def create_app(cfg: Config | None = None):
    cfg = cfg or load_config()
    setup_logging(cfg.log_level, cfg.log_format)
    service = Service(cfg)
    publisher = HAPublisher(cfg)

    def _publish_ha() -> None:
        try:
            publisher.publish(compute_state(service.db, cfg))
        except Exception as exc:                       # noqa: BLE001
            log.warning("HA publish failed: %s", exc)

    # Poll every interval_seconds while it's quiet, every fast_interval_seconds
    # while something is going on (service.threat_active): a minute's delay
    # costs nothing at noon and a lot during an attack.
    poll = {"seconds": cfg.poll.interval_seconds, "scheduler": None}

    def _poll_seconds() -> int:
        try:
            fast = service.threat_active()
        except Exception as exc:                       # noqa: BLE001
            log.warning("threat check failed: %s", exc)
            fast = False
        return (min(cfg.poll.fast_interval_seconds, cfg.poll.interval_seconds)
                if fast else cfg.poll.interval_seconds)

    async def _poll_and_publish() -> None:
        await service.ingest_once()
        await asyncio.to_thread(_publish_ha)
        seconds = await asyncio.to_thread(_poll_seconds)
        if seconds != poll["seconds"] and poll["scheduler"] is not None:
            poll["seconds"] = seconds
            poll["scheduler"].reschedule_job("poll", trigger="interval", seconds=seconds)
            log.info("polling every %ds (%s)", seconds,
                     "threat active" if seconds < cfg.poll.interval_seconds else "quiet")

    async def _prune() -> None:
        await asyncio.to_thread(service.prune)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        upgrade_task = None
        if cfg.poll.reparse_on_start:
            await asyncio.to_thread(service.reparse_all)
            db.set_meta("parsed_by", __version__)
        elif db.get_meta("parsed_by") != __version__:
            # a new version usually means parser fixes: re-read the recent
            # stored posts once, so the map isn't missing what the old parser
            # dropped (cheap - two days of posts, not the whole history).
            # First correct stored texts the old scraper got wrong (replies
            # saved as the message they quote) - a minute of polite paging,
            # so it runs in the background instead of holding up startup.
            async def _upgrade():
                try:
                    await service.repair_recent_texts(pages=12)
                    await asyncio.to_thread(service.reparse_since, 48)
                    db.set_meta("parsed_by", __version__)   # only once it finished
                except Exception as exc:                    # noqa: BLE001
                    log.warning("post-update re-parse failed: %s", exc)
            upgrade_task = asyncio.create_task(_upgrade())
        if publisher.start():
            await asyncio.to_thread(_publish_ha)

        scheduler = AsyncIOScheduler(timezone="UTC")
        scheduler.add_job(
            _poll_and_publish, "interval",
            seconds=cfg.poll.interval_seconds,
            next_run_time=datetime.now(timezone.utc),
            max_instances=1, coalesce=True, id="poll",
        )
        poll["scheduler"] = scheduler
        if publisher.enabled:
            scheduler.add_job(_publish_ha, "interval",
                              seconds=cfg.mqtt.publish_interval, id="ha")
        scheduler.add_job(_prune, "interval", hours=6, id="prune",
                          next_run_time=datetime.now(timezone.utc) + timedelta(minutes=5))
        scheduler.start()
        log.info("polling every %ss; UI on http://%s:%d",
                 cfg.poll.interval_seconds, cfg.server.host, cfg.server.port)
        try:
            yield
        finally:
            if upgrade_task is not None and not upgrade_task.done():
                # a stop mid-upgrade: it isn't marked done, so it re-runs on
                # the next start
                try:
                    await asyncio.wait_for(upgrade_task, 5)
                except BaseException:                  # noqa: BLE001
                    pass
            scheduler.shutdown(wait=False)
            publisher.close()
            await service.aclose()

    app = FastAPI(title="DroneVisualizer", version=__version__, lifespan=lifespan)

    # live data must never come from a cache - the HA companion app's WebView
    # and some proxies will otherwise happily reuse a GET for /api/clusters.
    # The page itself (html/js/css) must be revalidated on every load, or a
    # phone keeps running the old app.js after an add-on update (a cheap 304
    # via the ETag when nothing changed).
    @app.middleware("http")
    async def _no_store_api(request, call_next):
        resp = await call_next(request)
        if request.url.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        elif "cache-control" not in resp.headers:
            resp.headers["Cache-Control"] = "no-cache"
        return resp

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
                 "color": COLOR[s], "family": FAMILY[s],
                 "speed_kmh": cfg.dedupe.speed_kmh.get(s, SPEED_KMH.get(s))}
                for s in ALL_SLUGS
            ],
            "tile_url": cfg.server.tile_url,
            "tile_attribution": cfg.server.tile_attribution,
            "tile_url_dark": cfg.server.tile_url_dark,
            "tile_attribution_dark": cfg.server.tile_attribution_dark,
            "map_theme": cfg.server.map_theme,
            "poll_interval": cfg.poll.interval_seconds,
            "home": list(cfg.home) if cfg.home else None,
            "version": __version__,
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

    @app.get("/api/ha")
    def api_ha():
        """The same snapshot published to MQTT - threats in the configured
        area, rollups, alarm state."""
        return compute_state(db, cfg)

    @app.get("/api/health")
    def api_health():
        row = db.query_one(
            "SELECT (SELECT COUNT(*) FROM raw_message) rm, "
            "(SELECT COUNT(*) FROM event) ev, (SELECT COUNT(*) FROM cluster) cl, "
            "(SELECT COUNT(*) FROM cluster WHERE resolved_at IS NULL) open_cl"
        )
        last = db.get_meta("last_ingest")
        lag = None
        if last:
            try:
                lag = round((datetime.now(timezone.utc) - dtp.isoparse(last)).total_seconds())
            except Exception:
                pass
        ok = (lag is None or lag < cfg.poll.interval_seconds * 4)
        return {
            "status": "ok" if ok else "degraded",
            "version": app.version,
            "poll_interval_s": cfg.poll.interval_seconds,
            "last_ingest": last,
            "ingest_lag_s": lag,
            "last_ingest_stats": service.last_ingest_stats,
            "last_error": service.last_error,
            "db_bytes": db.size_bytes(),
            "counts": {"raw_messages": row["rm"], "events": row["ev"],
                       "clusters": row["cl"], "open_clusters": row["open_cl"]},
            "mqtt": {"enabled": publisher.enabled, "connected": publisher.connected},
            "channels": cfg.sources.channels,
            "retain_days": cfg.retain_days,
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
        include_resolved: bool = False,
        ref: str | None = None,
    ):
        """``ref=lat,lon`` (the viewer's location) is the point each track's
        danger is assessed against; without it, the area's centre."""
        t0 = _parse_time(since, default_hours=6)
        t1 = _parse_time(until, default_hours=0) if until else datetime.now(timezone.utc)
        area_obj = resolve_area(cfg, area)
        want_threats = set(_csv(threats) or [])
        want_channels = set(_csv(channels) or [])

        sql = ("SELECT * FROM cluster WHERE last_posted_at >= ? AND first_posted_at <= ? "
               + ("" if include_resolved else "AND resolved_at IS NULL ")
               + "ORDER BY last_posted_at DESC")
        rows = db.query(sql, (t0.isoformat(), t1.isoformat()))
        # A live missile threat stays on the map past a short window ("5 min")
        # until it's resolved / has hit, or its hold time runs out - a
        # "Загроза ОТРК, Курськ" must not vanish 5 minutes in.
        if not until:
            seen = {r["id"] for r in rows}
            hold_from = (t1 - timedelta(minutes=max(THREAT_HOLD_MIN.values()))).isoformat()
            for r in db.query(
                "SELECT * FROM cluster WHERE last_posted_at >= ? AND last_posted_at < ? "
                "AND resolved_at IS NULL AND status <> 'impact' ORDER BY last_posted_at DESC",
                (hold_from, t0.isoformat()),
            ):
                hold = THREAT_HOLD_MIN.get(FAMILY.get(r["threat_type"]))
                if hold is None or r["id"] in seen:
                    continue
                if dtp.isoparse(r["last_posted_at"]) >= t1 - timedelta(minutes=hold):
                    rows.append(r)

        # cheap filters first, so we only pull events for clusters we will show
        kept = []
        for c in rows:
            if want_threats and c["threat_type"] not in want_threats:
                continue
            if located_only and c["centroid_lat"] is None:
                continue
            if area_obj and not (cluster_touches_area(area_obj, c)
                                 or _launch_aims_at(c, area_center(area_obj))):
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

        # a fix on a big city's centre ("Київ", not "Позняки") only says
        # "somewhere in the city" - no course is projected from it
        coarse_places = {pl.name for pl in service.gaz.places
                         if pl.kind == "city" and pl.rank >= 5}
        ref_pt, ref_kind = _latlon(ref), "here"
        if ref_pt is None and cfg.home is not None:
            ref_pt, ref_kind = cfg.home, "home"      # from Home Assistant
        if ref_pt is None and area_obj is not None:
            ref_pt, ref_kind = area_center(area_obj), "area"
        now = datetime.now(timezone.utc)
        out = []
        for c in kept:
            evs = by_cluster.get(c["id"], [])
            if min_conf and max((e["parse_confidence"] for e in evs), default=0.0) < min_conf:
                continue
            out.append(_cluster_dto(c, evs, speed_kmh=cfg.dedupe.speed_kmh,
                                    ref=ref_pt, now=now,
                                    max_age_min=cfg.alarm.active_minutes,
                                    coarse_places=coarse_places))
            out[-1]["missile"] = _missile_info(c, evs, ref_pt, now)
        return {
            "count": len(out), "clusters": out,
            "poll_s": poll["seconds"],              # the server's current pace
            "ref": ({"lat": ref_pt[0], "lon": ref_pt[1], "kind": ref_kind}
                    if ref_pt else None),
        }

    @app.get("/api/aviation")
    def api_aviation():
        """Missile-carrier aircraft in the air, as timers since takeoff
        (relocations excluded) - see aviation.py."""
        now = datetime.now(timezone.utc)
        rows = db.query(
            "SELECT channel, url, posted_at, text FROM raw_message WHERE posted_at >= ? "
            "ORDER BY posted_at", ((now - timedelta(hours=12)).isoformat(),))
        posts = [(dtp.isoparse(r["posted_at"]), r["channel"], r["url"], r["text"]) for r in rows]
        return {"alerts": active_alerts(posts, now)}

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
                f"heading_deg, count, lat, lon, altitude_m, cluster_id FROM event WHERE raw_message_id IN ({ph}) "
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
                        "lat": e["lat"], "lon": e["lon"], "altitude_m": e["altitude_m"],
                        "cluster_id": e["cluster_id"],
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
        return {"ingested": await service.ingest_once(wait=True)}

    @app.post("/api/reparse")
    async def api_reparse(since_hours: float | None = None):
        """Full rebuild by default; pass ``?since_hours=6`` for an incremental
        reparse of just the recent window (keeps the map populated)."""
        if since_hours and since_hours > 0:
            return await asyncio.to_thread(service.reparse_since, since_hours)
        return await asyncio.to_thread(service.reparse_all)

    if WEB_DIR.exists():
        # The page links its own js/css with ?v=<version>: a phone (HA app
        # WebView) that cached an older app.js can't keep running it after an
        # add-on update, because the new page asks for a different URL.
        index_html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        for asset in ("style.css", "app.js"):
            index_html = index_html.replace(f'"{asset}"', f'"{asset}?v={__version__}"')

        @app.get("/", include_in_schema=False)
        @app.get("/index.html", include_in_schema=False)
        def index():
            return HTMLResponse(index_html)

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


def _launch_aims_at(c, target: tuple[float, float]) -> bool:
    """A missile threat sitting at a launch area ("Загроза балістики з
    Курська") counts for an area that area is typically used against - it
    used to be filtered out as "in Russia", so Kyiv saw nothing."""
    if FAMILY.get(c["threat_type"]) not in ("ballistic", "cruise"):
        return False
    site = site_at(c["centroid_lat"], c["centroid_lon"])
    return site is not None and reaches(site, target)


# a launch-area threat nobody follows up within this long is likely a false alarm
QUIET_THREAT_MIN = 10.0



def _missile_info(c, evs, ref: tuple[float, float] | None,
                  now: datetime | None = None) -> dict | None:
    """Launch area -> target for a ballistic / cruise missile track, for the
    map's trajectory line. ``evs`` newest first.

    * launch: the launch area of its first fix, if it started at one
    * target: the stated destination; else where it's descending; else its
      latest named place past the launch area; else the viewer's point / the
      area, when that's a region this launch area is typically used on
    * final: after "спуск" / impact the trajectory doesn't change any more
    * candidates: no launch area known ("Київ — спуск балістики") - the usual
      areas a ballistic missile on that target comes from
    * maybe_false: "Загроза ОТРК, Курськ" and then nothing - no target, no
      descent, no new report - for QUIET_THREAT_MIN: likely a false alarm
    """
    fam = FAMILY.get(c["threat_type"])
    if fam not in ("ballistic", "cruise"):
        return None
    fixes = [(e["lat"], e["lon"]) for e in reversed(evs) if e["lat"] is not None]
    if not fixes and c["centroid_lat"] is None:
        return None
    first = fixes[0] if fixes else (c["centroid_lat"], c["centroid_lon"])
    here = (c["centroid_lat"], c["centroid_lon"])
    site = site_at(*first)
    final = c["status"] in ("descending", "impact")

    to, to_name, to_kind = None, None, None
    if c["dest_lat"] is not None:
        to, to_name, to_kind = (c["dest_lat"], c["dest_lon"]), c["dest_name"], "dest"
    elif here[0] is not None and (final or site is None or site_at(*here) != site):
        to, to_name, to_kind = here, c["place_name"], "place"
    elif site is not None and ref is not None and reaches(site, ref):
        to, to_name, to_kind = ref, None, "area"

    zircon = any("циркон" in (e["raw_line"] or "").lower() for e in evs)
    speed = (HYPERSONIC_AVG_KMH if zircon else BALLISTIC_AVG_KMH if fam == "ballistic"
             else SPEED_KMH.get(c["threat_type"], 750.0))
    info = {"final": final, "to": None, "to_name": to_name, "to_kind": to_kind,
            "launch": None, "km": None, "min": None, "candidates": [],
            "maybe_false": False}
    if site is not None and not final and to_kind == "area":
        try:
            quiet = ((now or datetime.now(timezone.utc))
                     - dtp.isoparse(c["last_posted_at"])).total_seconds() / 60.0
            info["maybe_false"] = quiet >= QUIET_THREAT_MIN
        except (TypeError, ValueError):
            pass
    if to is not None:
        info["to"] = [round(to[0], 4), round(to[1], 4)]
    if site is not None:
        info["launch"] = {"name": site.name, "lat": site.lat, "lon": site.lon}
        if to is not None:
            km = haversine_km((site.lat, site.lon), to)
            info["km"] = round(km)
            info["min"] = round(km / speed * 60.0, 1)
    elif to is not None and fam == "ballistic":
        info["candidates"] = [{"name": s.name, "lat": s.lat, "lon": s.lon,
                               "km": round(haversine_km((s.lat, s.lon), to)),
                               "min": round(haversine_km((s.lat, s.lon), to) / speed * 60.0, 1)}
                              for s in candidates(to)]
    return info


def _cluster_dto(c, evs, *, speed_kmh: dict[str, float] | None = None,
                 ref: tuple[float, float] | None = None,
                 now: datetime | None = None,
                 max_age_min: float | None = None,
                 coarse_places: set[str] | None = None) -> dict:
    """``evs`` is this cluster's events, newest first."""
    now = now or datetime.now(timezone.utc)
    last = c["last_posted_at"]
    age_min = None
    try:
        age_min = round((now - dtp.isoparse(last)).total_seconds() / 60, 1)
    except Exception:
        pass

    # movement guess: successive reported positions, oldest -> newest,
    # consecutive duplicates collapsed
    # consecutive duplicates collapsed; track_src[i] is the post that put the
    # track at track[i] (so a leg i -> i+1 can say which report drew it)
    track: list[list[float]] = []
    track_src: list[dict] = []
    for e in reversed(evs):
        if e["lat"] is None:
            continue
        pt = [round(e["lat"], 4), round(e["lon"], 4)]
        if not track or track[-1] != pt:
            track.append(pt)
            track_src.append({"channel": e["channel"], "url": e["url"],
                              "posted_at": e["r_posted"], "place": e["place_name"],
                              "line": e["raw_line"]})

    # course / speed / uncertainty from the timed fixes (see predict.py)
    m = cluster_motion(c, evs, speed_kmh or SPEED_KMH)
    heading = m.heading_deg if m.heading_deg is not None else c["heading_deg"]
    now_pos = dead_reckon(m, now)
    motion = m.to_dict()
    motion["now"] = [round(now_pos[0], 4), round(now_pos[1], 4)] if now_pos else None
    # No forecast wedge for drones: their reports are a place name every few
    # minutes, often "toward the city from the north" - a 15-20 minute wedge
    # from that was mostly noise (e.g. running on south past Kyiv). Nor from a
    # whole-city fix, which says nothing about where in the city it is; there
    # even the short heading arrow and the "probably here now" mark go.
    coarse = bool(coarse_places) and c["place_name"] in coarse_places
    # "unknown" = an untyped sighting ("🅿️1х Козин"), in practice a drone
    drone = FAMILY.get(c["threat_type"]) in ("drone", "unknown")
    motion["cone"] = ([] if coarse or drone
                      else [[round(p[0], 4), round(p[1], 4)] for p in cone(m)])
    motion["projection"] = not coarse
    if coarse or drone:
        motion["now"] = None

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
        "heading_observed": m.heading_src == "observed",
        "track": track,
        "track_src": track_src,
        "motion": motion,
        "danger": assess(m, ref, now, max_age_min) if ref else None,
        "count": c["count"],
        "count_max": c["count_max"],
        "altitude_m": c["altitude_m"],
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
