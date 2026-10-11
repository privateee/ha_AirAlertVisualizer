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

import re

from ..config import Config
from ..geo.gazetteer import Gazetteer, PlaceHit
from ..geo.util import bearing_deg, compass, dest_point
from ..log import get_logger
from .directions import analyze, is_clear
from .normalize import (HINT_CIRCLE, extract_count, fold, normalize, split_lines,
                        strip_emoji, strip_hints)
from .threats import FAMILY, ThreatMatch, classify_line

log = get_logger("parse")

_UNKNOWN_MATCH = ThreatMatch("unknown", "", 0)

# Recap / aftermath / siren-status posts that must NOT become map clusters
# (they still show in the feed). Deliberately narrow.
_SUMMARY_RX = re.compile(
    r"\bза\s+(нич|ничь|добу|минул)"
    r"|\bминул[аоу]\w*\s+ноч"
    r"|\bунаслидок|\bвнаслидок"
    r"|\bстаном\s+на\s+\d"
    r"|\bпидсумк|\bподсчет|\bпидрахун"
    r"|\b(усього|всього|загалом)\s+(збит|знищен|сбит|уничтожен|випуст|запуст)"
    r"|\b(збит|знищен|сбит|уничтожен)\w*\s+\d{2,}"
    r"|\bповитрян\w*\s+тривог\w*\s*$"
    r"|\bвидбий\s+тривог"
    r"|\bдовидков|\bинфографик|\bстатистик",
    re.I,
)


# "в напрямку міста", "на місто", "до міста", "курсом на місто" (folded)
_TO_CITY_RX = re.compile(
    r"\b(?:[ву]\s+напрям\w*|[ву]\s+бик|курс\w*\s+на|на|до)\s+мист[аоу]\b")
# A drone known only by "target + side" is put this far out on that side -
# outside a city's edge, a few minutes' flight in.
APPROACH_KM = 15.0
_FROM_SIDE = {
    "N": "з півночі", "NE": "з північного сходу", "E": "зі сходу",
    "SE": "з південного сходу", "S": "з півдня", "SW": "з південного заходу",
    "W": "із заходу", "NW": "з північного заходу",
}


# Lines that are a channel's furniture, not a report (matched on the folded
# line): footers, subscribe / support / "send us news" links, promo for other
# channels, and siren on/off announcements.
_BOILERPLATE_RX = re.compile(
    r"надислати новину|пидписати|пидписатис|подписат|дивитис\w* график"
    r"|пидтримати канал|слидкувати за|онлайн-карт|детальнише по руху|канал, де пишуть"
    r"|побачили якус|надсилайте нам|monobank|https?://|t\.me/|@\w+_bot\b"
    r"|оголошен\w* повитрян\w* тривог|видбий тривог|отбой тревог|объявлен\w* воздушн\w* тревог",
)
# what's left of a line after emoji / filler ("ㅤ", arrows) are stripped
_FILLER_RX = re.compile(r"[\sㅤㅤ⁠​·•|/\\-]*")
# ... and the same furniture *inside* a line: an edited post can come as one
# line, "🚨 КИЇВСЬКА ОБЛАСТЬ - ОГОЛОШЕНА ПОВІТРЯНА ТРИВОГА Бпла на Переяслав
# ㅤ Надіслати новину @bot 👉ПІДПИСАТИСЯ" - cut the phrases, keep the report.
_CUT_RX = re.compile(
    r"(?:[🚨❎]\s*)?(?:(?:м\.\s*)?[А-ЯІЇЄҐA-Z][^\n]{0,30}?\s+-\s+)?"
    r"(?:оголошен\w*\s+повітрян\w*\s+тривог\w*|відбій\s+тривог\w*)"
    r"|(?:👉\s*)?надіслати новину.*$|(?:➡️?\s*)?дивитись графіки.*$"
    r"|(?:👉\s*)?підписати\w*.*$",
    re.I | re.M,
)


def strip_boilerplate(text: str) -> str:
    keep = []
    for line in _CUT_RX.sub(" ", text or "").splitlines():
        # folded for the words, plain lowercase for links (fold spaces out "/")
        if _BOILERPLATE_RX.search(fold(line)) or _BOILERPLATE_RX.search(line.lower()):
            continue
        if _FILLER_RX.fullmatch(strip_emoji(line)):
            continue
        keep.append(line)
    return "\n".join(keep).strip()


# News / aftermath / ads in a city news channel (folded - no "ь", і -> и):
# damage and casualties, power and transport, prices, politics and quotes.
_NEWS_RX = re.compile(
    r"уламк|постраждал|поранен|загинул|загибл|госпитализ|влучан|пожеж|\bгорит|згорив"
    r"|\bудар|\bдим\b|видключен|график|енерг|свитл|пидстанц|водопостач|метро|тролейбус"
    r"|трамва|транспорт|знижк|\bгрн\b|\bдтп\b|затримал|синоптик|погод|нафт"
    r"|кличк|зеленск|трамп|путин|\bрф\b|\bроси[яиюе]\b|кмда|експерт|\bзми\b|нардеп"
    r"|укренерго|\bтец\b|евакуац|опалювал|наслидк|[ву]насл\w*\s+атак|писля\s+атак|кадри"
    # reported speech and outage words: "В КЦ повідомили, що в Києві ввели
    # екстрені" (emergency power cuts) - short, names Київ, but it's news
    r"|повидомил|повидомля|заявил|заявив|розповив|\bввели\b|екстрен|аварийн|стабилизац"
    r"|напруг",                     # "Напруга зараз місцями по Києву" - power is back
)
_TRACK_LINE_RX = re.compile(r"бпла|реактив|шахед|мопед|курс\w*\s+на")


def is_news(text: str, folded: str) -> bool:
    """A news-channel post that is not a tracking report."""
    if _NEWS_RX.search(folded):
        return True
    # long prose with at most one tracking line is an article, not a report
    tracking = sum(1 for ln in folded.splitlines() if _TRACK_LINE_RX.search(ln))
    return len(text) > 300 and tracking < 2


def is_summary(folded_text: str) -> bool:
    return bool(_SUMMARY_RX.search(folded_text))


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
    altitude_m: int | None = None

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
            "parse_confidence": self.parse_confidence, "altitude_m": self.altitude_m,
        }


# Target altitude. Only unambiguous forms: an explicit "висота 400 м" /
# "высота 1,5 км", or war_monitor's bare trailing "Чайки 400м" / "Теремки
# 3,2км". A trailing number after "за / від / до / в ..." is a distance
# ("за 10 км від Києва"), not a height.
_ALT_EXPLICIT = re.compile(
    r"\bвисот\w*\s*(?:~|до|близко|около|приблизно|бл\.?)?\s*(\d+(?:[.,]\d+)?)\s*(км|м)\b")
_ALT_TRAILING = re.compile(
    r"(?<!\bза\s)(?<!\bвид\s)(?<!\bот\s)(?<!\bдо\s)(?<!\bв\s)(?<!\bу\s)(?<!\bна\s)"
    r"(?<![\d.,])(\d+(?:[.,]\d+)?)\s*(км|м)\.?\s*$")


def extract_altitude(folded_line: str) -> int | None:
    """Altitude in metres from a *folded* line, or None."""
    m = _ALT_EXPLICIT.search(folded_line) or _ALT_TRAILING.search(folded_line)
    if not m:
        return None
    try:
        v = float(m.group(1).replace(",", "."))
    except ValueError:
        return None
    metres = v * 1000 if m.group(2) == "км" else v
    return int(round(metres)) if 10 <= metres <= 15000 else None


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
        # footers ("Надіслати новину @bot / 👉ПІДПИСАТИСЯ"), promo links and
        # siren on/off lines carry no report - and "новину" matched the
        # village "Нове", "Слідкувати за БПЛА" became a drone
        text = strip_boilerplate(text)
        if not text:
            return []
        folded_full = fold(text)

        # recap / statistics / siren-status posts stay in the feed but never
        # produce map markers
        if is_summary(folded_full):
            return []

        # A city news channel (kievreal1): news, aftermath and ads stay in the
        # feed, only the short tracking posts are read - and those name just
        # a place ("Вишгород", "Чабани зниження"), like a terse channel.
        news = channel in self.cfg.parse.news_channels
        if news and is_news(text, folded_full):
            return []

        oblast_hint = self.gaz.detect_oblast(folded_full)
        _rh = self.gaz.region_hit(folded_full)       # (label, (lat, lon)) | None
        region = (_rh[0], _rh[1][0], _rh[1][1]) if _rh else None
        terse = news or channel in self.cfg.parse.terse_channels

        # A short leading line that names a *precise* place ("Бровари:",
        # "Обухів —") is a good fallback position. Region words no longer
        # resolve to a place, so this only picks up real settlements. A first
        # line that is itself a report ("Реактивний БпЛА курсом на Трипілля")
        # is not a header - it used to become every later line's position.
        header_place: PlaceHit | None = None
        display = normalize(text, keep_case=True)
        first_line = display.split("\n", 1)[0]
        if first_line.endswith(":") or (
                len(first_line) <= 40 and classify_line(fold(first_line)) is None):
            hh = self.gaz.find(fold(first_line), oblast_hint=oblast_hint,
                               area_center=area_center)
            header_place = next((h for h in hh if h.place.kind != "oblast"), None)

        # "Загальна по мопедам:" declares the threat type for every line that
        # follows without repeating it ("3 ... на Ковель", not "3 мопеда ...
        # на Ковель"). Capture it from the header line only, and only a real
        # RULES match - not classify_line's generic-motion "unknown" fallback.
        header_tm = classify_line(fold(first_line))
        post_default_type = (
            header_tm.slug if header_tm is not None and header_tm.slug != "unknown" else None
        )

        events: list[ParsedEvent] = []
        lines = split_lines(display)
        for li, line in enumerate(lines):
            fline = fold(line)
            hits = self.gaz.find(fline, oblast_hint=oblast_hint,
                                 area_center=area_center)
            if not hits and not self.gaz.region_hit(fline):
                # "Ьориспіль", "Святощин": one key off a known name - but not a
                # region word ("на Київщину" read as a misspelt "Києву")
                hits = self.gaz.typo_hits(line, fline, area_center=area_center)

            tm = classify_line(fline)

            # a *pure* "all clear" for a place (no threat words on the line)
            # -> a marker the deduper uses to resolve matching open clusters
            if tm is None and hits and is_clear(fline):
                h = hits[0]
                events.append(ParsedEvent(
                    threat_type="clear", threat_raw=None, count=None,
                    status="clear", place_name=h.place.name,
                    lat=h.place.lat, lon=h.place.lon, geo_confidence=0.8,
                    raw_line=strip_hints(line)[:400], parse_method="rules",
                    parse_confidence=0.7,
                ))
                continue

            if tm is None:
                # a bare "Київ:" header over the reports is not itself a sighting
                # only short lines count without a threat word ("Вишгород",
                # "Чабани зниження") - in a news channel prose that merely
                # names a place is a story, not a sighting
                header_only = li == 0 and len(lines) > 1 and first_line.rstrip().endswith(":")
                # ... and in a news channel the place must be specific: its
                # sightings name a district / village ("Вишгород", "Чабани
                # зниження"); a bare "Київ" / "області" is a news line
                # ("Напруга зараз місцями по Києву та області")
                coarse_only = news and all(
                    h.place.kind == "oblast" or (h.place.kind == "city" and h.place.rank >= 5)
                    for h in hits)
                if (terse and hits and not header_only and not coarse_only
                        and (not news or len(line) <= 45)):
                    tm = _UNKNOWN_MATCH
                else:
                    continue
            if tm.slug == "unknown" and post_default_type:
                tm = ThreatMatch(post_default_type, tm.raw, tm.start)
            dr = analyze(fline, hits)
            if HINT_CIRCLE in fline and dr.status in ("moving", "unknown"):
                dr.status = "circling"            # war_monitor's 🔄 marker

            # everything below works with plain (name, lat, lon) triples
            pos = _triple(dr.primary)
            dest = _triple(dr.dest)
            src = _triple(dr.src)
            heading = dr.heading_deg
            geo_conf = _geo_conf(dr.primary, from_header=False)

            # "Київ: реактивний БпЛА в напрямку міста з півночі" - "the city"
            # is the header's city, and it's the *target*: the drone is
            # outside it, coming in. It used to be put on the city centre,
            # so the course projection ran on past the city ("0 km, moving away").
            city = _triple(header_place) or pos
            if city is not None and _TO_CITY_RX.search(fline):
                dest = city
                if pos is not None and pos[1:] == city[1:]:
                    pos = None

            if pos is None and dest is not None and src is None and heading is not None:
                # target + side of approach only ("на Київ з півночі"): place
                # it on that side, outside the target, heading in - not on it
                back = (heading + 180.0) % 360.0
                lat, lon = dest_point(dest[1], dest[2], back, APPROACH_KM)
                pos = (f"{dest[0]} ({_FROM_SIDE.get(compass(back), compass(back))})", lat, lon)
                geo_conf = 0.45

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
                elif region is not None and not news and not (li == 0 and len(lines) > 1):
                    # last resort: vague area centre - but never for the header
                    # line of a multi-line post ("Загальна по мопедам:"), which
                    # would become a phantom track at an oblast named below
                    pos, geo_conf = region, 0.35
                elif src is not None:
                    pos, src = src, None

            if dest is not None and pos is not None and dest[0] == pos[0]:
                dest = None
            if src is not None and pos is not None and src[0] == pos[0]:
                src = None

            count = extract_count(line)
            # a bare threat-word mention with no count, no position, no
            # source, no destination is a section header ("Загальна по
            # мопедам:"), not a report - drop it rather than emit a phantom
            # placeless/countless event.
            if count is None and pos is None and dest is None and src is None:
                continue

            ev = ParsedEvent(
                threat_type=tm.slug,
                threat_raw=tm.raw or None,
                count=count,
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
                raw_line=strip_hints(line)[:400],
                parse_method="rules",
                altitude_m=extract_altitude(fline),
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
