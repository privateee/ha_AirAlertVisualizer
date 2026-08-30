"""Post text -> list[ParsedEvent].

    normalize -> split into lines -> per line:
        classify threat  (parse/threats.py)
        find toponyms     (geo/gazetteer.py)
        analyse direction (parse/directions.py)
    -> ParsedEvent

An optional LLM pass (parse/llm.py) fills gaps when the rules find nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Config
from ..geo.gazetteer import Gazetteer, PlaceHit
from ..geo.util import bearing_deg
from ..log import get_logger
from .directions import analyze
from .normalize import extract_count, fold, normalize, split_lines
from .threats import FAMILY, ThreatMatch, classify_line

log = get_logger("parse")

_UNKNOWN_MATCH = ThreatMatch("unknown", "", 0)


@dataclass(slots=True)
class ParsedEvent:
    threat_type: str
    threat_raw: str | None
    count: int | None
    status: str
    place_name: str | None
    lat: float | None
    lon: float | None
    geo_confidence: float
    src_name: str | None = None
    src_lat: float | None = None
    src_lon: float | None = None
    dest_name: str | None = None
    dest_lat: float | None = None
    dest_lon: float | None = None
    heading_deg: float | None = None
    raw_line: str = ""
    parse_method: str = "rules"
    parse_confidence: float = 0.0

    @property
    def family(self) -> str:
        return FAMILY.get(self.threat_type, "unknown")

    def to_row(self) -> dict:
        return {
            "threat_type": self.threat_type, "threat_raw": self.threat_raw,
            "count": self.count, "status": self.status,
            "place_name": self.place_name, "lat": self.lat, "lon": self.lon,
            "geo_confidence": self.geo_confidence,
            "src_name": self.src_name, "src_lat": self.src_lat, "src_lon": self.src_lon,
            "dest_name": self.dest_name, "dest_lat": self.dest_lat,
            "dest_lon": self.dest_lon, "heading_deg": self.heading_deg,
            "raw_line": self.raw_line, "parse_method": self.parse_method,
            "parse_confidence": self.parse_confidence,
        }


def _geo_conf(hit: PlaceHit | None, *, from_header: bool) -> float:
    if hit is None:
        return 0.0
    if from_header:
        return 0.4
    return 0.9 if hit.place.rank >= 3 else 0.8


class Parser:
    def __init__(self, cfg: Config, gaz: Gazetteer | None = None):
        self.cfg = cfg
        self.gaz = gaz or Gazetteer()
        self._llm = None
        if cfg.parse.llm.enabled and cfg.parse.llm.provider != "none":
            from .llm import LLMExtractor  # lazy: keep base install light

            self._llm = LLMExtractor(cfg, self.gaz)

    # -- public -----------------------------------------------------------
    def parse(
        self,
        text: str,
        *,
        channel: str | None = None,
        area_center: tuple[float, float] | None = None,
    ) -> list[ParsedEvent]:
        folded_full = fold(text)
        oblast_hint = self.gaz.detect_oblast(folded_full)
        _rh = self.gaz.region_hit(folded_full)       # (label, (lat, lon)) | None
        region = (_rh[0], _rh[1][0], _rh[1][1]) if _rh else None
        terse = channel in self.cfg.parse.terse_channels

        # A short leading line that names a *precise* place ("Бровари:",
        # "Обухів —") is a good fallback position. Region words no longer
        # resolve to a place, so this only picks up real settlements.
        header_place: PlaceHit | None = None
        display = normalize(text, keep_case=True)
        first_line = display.split("\n", 1)[0]
        if first_line.endswith(":") or len(first_line) <= 40:
            hh = self.gaz.find(fold(first_line), oblast_hint=oblast_hint,
                               area_center=area_center)
            header_place = next((h for h in hh if h.place.kind != "oblast"), None)

        events: list[ParsedEvent] = []
        for line in split_lines(display):
            fline = fold(line)
            hits = self.gaz.find(fline, oblast_hint=oblast_hint,
                                 area_center=area_center)
            tm = classify_line(fline)
            if tm is None:
                if terse and hits:
                    tm = _UNKNOWN_MATCH
                else:
                    continue
            dr = analyze(fline, hits)

            # everything below works with plain (name, lat, lon) triples
            pos = _triple(dr.primary)
            dest = _triple(dr.dest)
            src = _triple(dr.src)
            heading = dr.heading_deg
            geo_conf = _geo_conf(dr.primary, from_header=False)

            if pos is None:
                hp = _triple(header_place)
                if hp is not None:
                    # a precise leading place ("Бровари:") is where it is now
                    pos, geo_conf = hp, 0.55
                    if dest is not None and heading is None and dest[1:] != pos[1:]:
                        heading = bearing_deg((pos[1], pos[2]), (dest[1], dest[2]))
                elif dest is not None:
                    # "курсом на Обухів" with only a vague area around it: the
                    # target is the best-known point; a source / region word is
                    # merely the direction of approach.
                    origin = src or region
                    pos, dest, src = dest, None, origin
                    if origin and heading is None and origin[1:] != pos[1:]:
                        heading = bearing_deg((origin[1], origin[2]), (pos[1], pos[2]))
                    geo_conf = 0.6                  # projected, not confirmed there
                elif region is not None:
                    pos, geo_conf = region, 0.35    # last resort: vague area centre
                elif src is not None:
                    pos, src = src, None

            if dest is not None and pos is not None and dest[0] == pos[0]:
                dest = None
            if src is not None and pos is not None and src[0] == pos[0]:
                src = None

            ev = ParsedEvent(
                threat_type=tm.slug,
                threat_raw=tm.raw or None,
                count=extract_count(line),
                status=dr.status,
                place_name=pos[0] if pos else None,
                lat=pos[1] if pos else None,
                lon=pos[2] if pos else None,
                geo_confidence=geo_conf,
                src_name=src[0] if src else None,
                src_lat=src[1] if src else None,
                src_lon=src[2] if src else None,
                dest_name=dest[0] if dest else None,
                dest_lat=dest[1] if dest else None,
                dest_lon=dest[2] if dest else None,
                heading_deg=heading,
                raw_line=line.strip()[:400],
                parse_method="rules",
            )
            ev.parse_confidence = _parse_conf(ev)
            events.append(ev)

        events = _dedup_within_post(events)

        if self._llm is not None and self._llm.should_run(text, events):
            try:
                extra = self._llm.extract(text, area_center=area_center)
                events = _merge_llm(events, extra)
            except Exception as exc:  # never let the LLM break ingestion
                log.warning("LLM extraction failed: %s", exc)

        if self.cfg.parse.min_confidence > 0:
            events = [e for e in events if e.parse_confidence >= self.cfg.parse.min_confidence]
        return events


def _parse_conf(ev: ParsedEvent) -> float:
    has_place = ev.lat is not None
    if ev.threat_type != "unknown" and has_place:
        base = 0.85
    elif ev.threat_type != "unknown":
        base = 0.45
    elif has_place:
        base = 0.5
    else:
        base = 0.2
    if ev.heading_deg is not None:
        base = min(1.0, base + 0.05)
    if ev.geo_confidence and ev.geo_confidence < 0.5:
        base -= 0.15
    return round(max(0.0, base), 2)


def _triple(hit) -> tuple[str, float, float] | None:
    """(name, lat, lon) for a PlaceHit, or None."""
    if hit is None:
        return None
    return (hit.place.name, hit.place.lat, hit.place.lon)


def _dedup_within_post(events: list[ParsedEvent]) -> list[ParsedEvent]:
    seen: set[tuple] = set()
    out: list[ParsedEvent] = []
    for e in events:
        sig = (e.threat_type, e.place_name, e.dest_name, e.status)
        if sig in seen:
            continue
        seen.add(sig)
        out.append(e)
    return out


def _merge_llm(rules: list[ParsedEvent], llm: list[ParsedEvent]) -> list[ParsedEvent]:
    if not llm:
        return rules
    if not rules:
        return llm
    keys = {(e.threat_type, e.place_name) for e in rules}
    for e in llm:
        if (e.threat_type, e.place_name) not in keys:
            rules.append(e)
    return rules
