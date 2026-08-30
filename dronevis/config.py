"""Configuration loading.

Reads ``config.yaml`` from the project root (falling back to
``config.example.yaml``) and exposes it as nested dataclasses so the rest of
the code gets attribute access and sane defaults instead of dict spelunking.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent


_DEFAULT_CHANNELS = ["war_monitor", "AerisRimor", "kpszsu", "vanek_nikolaev"]


@dataclass(slots=True)
class SourcesConfig:
    backend: str = "tme_web"
    channels: list[str] = field(default_factory=lambda: list(_DEFAULT_CHANNELS))
    backfill_pages: int = 3
    user_agent: str = "Mozilla/5.0 DroneVisualizer/0.1"
    request_timeout: float = 20.0
    request_delay: float = 1.5


@dataclass(slots=True)
class PollConfig:
    interval_seconds: int = 60
    reparse_on_start: bool = False


@dataclass(slots=True)
class Area:
    key: str
    label: str
    center: tuple[float, float] | None = None
    radius_km: float | None = None
    bbox: tuple[float, float, float, float] | None = None  # S, W, N, E


@dataclass(slots=True)
class AreasConfig:
    default: str = "kyiv"
    defined: dict[str, Area] = field(default_factory=dict)


# Honest cruise speeds (km/h). "basic" attack UAVs (Shahed/мопед) stay under
# ~300; jet-powered ("реактивний"/turbo) ~500-600; the "Бандероль" small
# cruise missile is ~600, not Kalibr-fast. These are the hard reachability
# gate for trajectory chaining - Chernihiv->Poltava (~330 km) must not chain
# for anything short of a ballistic missile.
_DEFAULT_SPEED_KMH = {
    "shahed": 170.0, "jet_uav": 560.0, "recon_uav": 140.0,
    "cruise_missile": 620.0, "ballistic": 2000.0, "kab": 260.0,
    "aircraft": 700.0, "unknown": 220.0,
}


@dataclass(slots=True)
class DedupeConfig:
    time_window_minutes: int = 25
    max_span_minutes: int = 90          # a cluster never spans longer than this
    distance_km: float = 20.0           # two sightings this close = same object
    text_similarity: float = 0.72
    incompatible_split: bool = True
    # Trajectory chaining: "5 БпЛА повз Славутич" then "5 на Димер" a few
    # minutes later is the same group moving. Merge across a bigger distance
    # when the jump is downrange of the known heading and reachable at the
    # threat's speed within the elapsed time.
    trajectory: bool = True
    speed_kmh: dict[str, float] = field(default_factory=lambda: dict(_DEFAULT_SPEED_KMH))
    speed_slack: float = 1.25          # imprecision margin on speed * elapsed
    max_hop_km: float = 200.0          # a single sighting->sighting jump never
                                       # chains beyond this, whatever the gap
    heading_tolerance_deg: float = 55.0
    count_tolerance: int = 1           # |a-b| <= this counts as "same size"


@dataclass(slots=True)
class LLMConfig:
    enabled: bool = False
    provider: str = "none"
    endpoint: str = "http://localhost:11434/api/generate"
    model: str = "llama3.1:8b"
    api_key_env: str = "OPENAI_API_KEY"
    timeout: float = 45.0
    only_on_gap: bool = True


@dataclass(slots=True)
class ParseConfig:
    languages: list[str] = field(default_factory=lambda: ["uk", "ru"])
    min_confidence: float = 0.0
    # Channels that post bare toponyms with no threat word ("Соф борщага на
    # Вишневе."). For these, a line that names a place but no threat is still
    # treated as an (unidentified) air threat.
    terse_channels: list[str] = field(default_factory=lambda: ["AerisRimor"])
    llm: LLMConfig = field(default_factory=LLMConfig)


@dataclass(slots=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8750
    tile_url: str = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    tile_attribution: str = "&copy; OpenStreetMap contributors"
    # Dark basemap for night mode. Left EMPTY by default: the UI just inverts
    # the normal OSM tiles with a CSS filter, so night mode needs no extra CDN
    # and works offline. Point it at a real dark tile server if you have one.
    tile_url_dark: str = ""
    tile_attribution_dark: str = "&copy; OpenStreetMap contributors"
    map_theme: str = "dark"                 # dark | light (initial map theme)


@dataclass(slots=True)
class Config:
    sources: SourcesConfig
    poll: PollConfig
    areas: AreasConfig
    dedupe: DedupeConfig
    parse: ParseConfig
    server: ServerConfig
    database_path: Path
    log_level: str = "INFO"

    @property
    def default_area(self) -> Area:
        return self.areas.defined[self.areas.default]


def _as_tuple(value: Any) -> tuple | None:
    if value is None:
        return None
    return tuple(value)


def _default(cls: type, field_name: str) -> Any:
    """Field default of a (possibly slotted) dataclass. ``Cls.field`` returns a
    member descriptor under ``slots=True``, so read it from the field table."""
    return cls.__dataclass_fields__[field_name].default


def load_config(path: str | os.PathLike | None = None) -> Config:
    """Load configuration. ``path`` overrides the search order."""
    if path is None:
        path = os.environ.get("DRONEVIS_CONFIG") or None
    if path is not None:
        cfg_path = Path(path)
    else:
        cfg_path = PROJECT_ROOT / "config.yaml"
        if not cfg_path.exists():
            cfg_path = PROJECT_ROOT / "config.example.yaml"

    raw: dict[str, Any] = {}
    if cfg_path.exists():
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    src = raw.get("sources", {}) or {}
    sources = SourcesConfig(
        backend=src.get("backend", "tme_web"),
        channels=list(src.get("channels") or _DEFAULT_CHANNELS),
        backfill_pages=int(src.get("backfill_pages", 3)),
        user_agent=src.get("user_agent", _default(SourcesConfig, "user_agent")),
        request_timeout=float(src.get("request_timeout", 20.0)),
        request_delay=float(src.get("request_delay", 1.5)),
    )

    poll_raw = raw.get("poll", {}) or {}
    poll = PollConfig(
        interval_seconds=int(poll_raw.get("interval_seconds", 60)),
        reparse_on_start=bool(poll_raw.get("reparse_on_start", False)),
    )

    areas_raw = raw.get("areas", {}) or {}
    defined: dict[str, Area] = {}
    for key, a in (areas_raw.get("defined", {}) or {}).items():
        defined[key] = Area(
            key=key,
            label=a.get("label", key),
            center=_as_tuple(a.get("center")),
            radius_km=(float(a["radius_km"]) if a.get("radius_km") is not None else None),
            bbox=_as_tuple(a.get("bbox")),
        )
    if not defined:
        defined["ukraine"] = Area(
            key="ukraine", label="All Ukraine", bbox=(44.0, 22.0, 52.5, 40.3)
        )
    areas = AreasConfig(
        default=areas_raw.get("default", next(iter(defined))),
        defined=defined,
    )
    if areas.default not in defined:
        areas.default = next(iter(defined))

    dd = raw.get("dedupe", {}) or {}
    speed = dict(_DEFAULT_SPEED_KMH)
    speed.update({k: float(v) for k, v in (dd.get("speed_kmh", {}) or {}).items()})
    dedupe = DedupeConfig(
        time_window_minutes=int(dd.get("time_window_minutes", 25)),
        max_span_minutes=int(dd.get("max_span_minutes", 90)),
        distance_km=float(dd.get("distance_km", 20.0)),
        text_similarity=float(dd.get("text_similarity", 0.72)),
        incompatible_split=bool(dd.get("incompatible_split", True)),
        trajectory=bool(dd.get("trajectory", True)),
        speed_kmh=speed,
        speed_slack=float(dd.get("speed_slack", 1.25)),
        max_hop_km=float(dd.get("max_hop_km", 200.0)),
        heading_tolerance_deg=float(dd.get("heading_tolerance_deg", 55.0)),
        count_tolerance=int(dd.get("count_tolerance", 1)),
    )

    pr = raw.get("parse", {}) or {}
    llm_raw = pr.get("llm", {}) or {}
    llm = LLMConfig(
        enabled=bool(llm_raw.get("enabled", False)),
        provider=llm_raw.get("provider", "none"),
        endpoint=llm_raw.get("endpoint", _default(LLMConfig, "endpoint")),
        model=llm_raw.get("model", _default(LLMConfig, "model")),
        api_key_env=llm_raw.get("api_key_env", "OPENAI_API_KEY"),
        timeout=float(llm_raw.get("timeout", 45.0)),
        only_on_gap=bool(llm_raw.get("only_on_gap", True)),
    )
    parse = ParseConfig(
        languages=list(pr.get("languages", ["uk", "ru"])),
        min_confidence=float(pr.get("min_confidence", 0.0)),
        terse_channels=list(pr.get("terse_channels", ["AerisRimor"])),
        llm=llm,
    )

    sv = raw.get("server", {}) or {}
    server = ServerConfig(
        host=sv.get("host", "127.0.0.1"),
        port=int(sv.get("port", 8750)),
        tile_url=sv.get("tile_url", _default(ServerConfig, "tile_url")),
        tile_attribution=sv.get("tile_attribution", _default(ServerConfig, "tile_attribution")),
        tile_url_dark=sv.get("tile_url_dark", _default(ServerConfig, "tile_url_dark")),
        tile_attribution_dark=sv.get(
            "tile_attribution_dark", _default(ServerConfig, "tile_attribution_dark")
        ),
        map_theme=str(sv.get("map_theme", "dark")).lower(),
    )

    # relative DB path -> next to the config file when running from the repo,
    # otherwise the current working directory (so a pip-installed run doesn't
    # try to write inside site-packages). An absolute DRONEVIS_DB_PATH always
    # wins, via _apply_env_overrides below.
    db_raw = raw.get("database", {}) or {}
    db_path = Path(db_raw.get("path", "data/dronevis.db"))
    if not db_path.is_absolute():
        anchor = PROJECT_ROOT if (PROJECT_ROOT / "config.example.yaml").exists() else Path.cwd()
        db_path = anchor / db_path

    cfg = Config(
        sources=sources,
        poll=poll,
        areas=areas,
        dedupe=dedupe,
        parse=parse,
        server=server,
        database_path=db_path,
        log_level=str(raw.get("log_level", "INFO")).upper(),
    )
    _apply_env_overrides(cfg)
    return cfg


def _apply_env_overrides(cfg: Config) -> None:
    """DRONEVIS_* environment variables win over the YAML file. Lets the
    Home Assistant add-on / a plain Docker run configure everything without
    templating a config file."""
    e = os.environ.get

    if v := e("DRONEVIS_CHANNELS"):
        cfg.sources.channels = [c.strip() for c in v.split(",") if c.strip()]
    if v := e("DRONEVIS_BACKFILL_PAGES"):
        cfg.sources.backfill_pages = int(v)
    if v := e("DRONEVIS_POLL_INTERVAL"):
        cfg.poll.interval_seconds = int(v)
    if v := e("DRONEVIS_HOST"):
        cfg.server.host = v
    if v := e("DRONEVIS_PORT"):
        cfg.server.port = int(v)
    if v := e("DRONEVIS_TILE_URL"):
        cfg.server.tile_url = v
    if (v := e("DRONEVIS_TILE_URL_DARK")) is not None:
        cfg.server.tile_url_dark = v
    if v := e("DRONEVIS_MAP_THEME"):
        cfg.server.map_theme = v.lower()
    if v := e("DRONEVIS_LOG_LEVEL"):
        cfg.log_level = v.upper()
    if v := e("DRONEVIS_DB_PATH"):
        p = Path(v)
        cfg.database_path = p if p.is_absolute() else Path.cwd() / p

    area = cfg.areas.defined.get(cfg.areas.default)
    if area is not None:
        if v := e("DRONEVIS_AREA_CENTER"):
            try:
                lat, lon = (float(x) for x in v.split(","))
                area.center = (lat, lon)
                area.bbox = None
            except ValueError:
                pass
        if v := e("DRONEVIS_AREA_RADIUS_KM"):
            area.radius_km = float(v)
        if v := e("DRONEVIS_AREA_LABEL"):
            area.label = v
