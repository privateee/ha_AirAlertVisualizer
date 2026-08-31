"""Threat taxonomy and classifier.

The four channels describe the same objects with very different words, in
Ukrainian *and* Russian, with slang and typos. This module folds all of that
into a small set of stable slugs.

Every pattern below is written in the *folded* alphabet produced by
``normalize.fold`` (і/ї/ы -> и, є/э -> е, ґ -> г, apostrophes/ь/ъ dropped),
so one vocabulary list covers both languages.

Slugs and the vocabulary they absorb
------------------------------------
shahed         шахед / шахід / шахеди / шахедів / шахед-136 / shahed /
               герань / герань-2 / geran / мопед / мопєд / мопед-камікадзе /
               moped / бпла / ббпла / дрон / дрони / безпілотник / uav /
               "нереактивний" (explicitly non-jet)
jet_uav        реактив / реактиви / реактивів / реактивний / реактивні /
               реактивна ціль / реактів / реактівний / jet / джет /
               "реактивний БпЛА" / "реактивний мопед"  (Shahed-238 class)
recon_uav      розвідувальний БпЛА / розвідник / разведчик / орлан / zala /
               supercam / суперкам / мерлін / фурія / лелека
cruise_missile крилата ракета / кр / калібр / kalibr / х-101 / х-555 / х-59 /
               х-69 / х-22 / х-35 / іскандер-к / бандероль / мгкр / онікс
ballistic      балістика / балістична / іскандер(-м) / кинджал / кинджали /
               kn-23 / аеробалістична / с-300 (strike)
kab            каб / каби / умпк / умпб / керована авіабомба /
               "керованих авіаційних бомб" / фаб / д-30 / глайд-бомба
aircraft       тактична авіація / міг-31 / ту-95 / ту-160 / ту-22 /
               зліт авіації / стратегічна авіація / борт(и)
unknown        movement + location but no recognisable class
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# One row per threat slug. family = dedupe bucket (never merged across);
# speed_kmh = cruise speed used by the trajectory-chaining reachability gate.
#   slug            family       colour     label                        short        speed
_TAXONOMY: list[tuple] = [
    ("shahed",        "drone",    "#e2493d", "Shahed / attack UAV",       "Shahed",     170),
    ("jet_uav",       "drone",    "#b5179e", "Jet-powered UAV",           "Jet UAV",    560),
    ("recon_uav",     "drone",    "#f4a259", "Recon UAV",                 "Recon",      140),
    ("banderol",      "cruise",   "#4895ef", "Banderol cruise missile",   "Banderol",   600),
    ("kalibr",        "cruise",   "#3a86ff", "Kalibr cruise missile",     "Kalibr",     880),
    ("x101",          "cruise",   "#2667ff", "Kh-101 / 55 cruise missile", "Kh-101",    840),
    ("x22",           "cruise",   "#1d4ed8", "Kh-22 / 32 cruise missile", "Kh-22",     1100),
    ("cruise_missile", "cruise",  "#3a86ff", "Cruise missile",            "Cruise",     750),
    ("kinzhal",       "ballistic", "#c77dff", "Kinzhal aeroballistic",    "Kinzhal",   4000),
    ("iskander",      "ballistic", "#9d4edd", "Iskander / KN-23",         "Iskander",  2100),
    ("ballistic",     "ballistic", "#8338ec", "Ballistic missile",        "Ballistic", 2000),
    ("kab",           "bomb",     "#ffb703", "Guided bomb (KAB / FAB)",   "KAB",        260),
    ("aircraft",      "aircraft", "#2a9d8f", "Crewed aircraft",           "Aircraft",   700),
    ("unknown",       "unknown",  "#8d99ae", "Unidentified air threat",   "Other",      220),
]

FAMILY = {r[0]: r[1] for r in _TAXONOMY}
COLOR = {r[0]: r[2] for r in _TAXONOMY}
LABEL = {r[0]: r[3] for r in _TAXONOMY}
SHORT = {r[0]: r[4] for r in _TAXONOMY}
SPEED_KMH = {r[0]: float(r[5]) for r in _TAXONOMY}
ALL_SLUGS = [r[0] for r in _TAXONOMY]

# pseudo-slug: "all clear" markers (not a filterable threat type)
COLOR["clear"] = "#3ddc84"
LABEL["clear"] = "All clear"
SHORT["clear"] = "Clear"


@dataclass(slots=True)
class ThreatRule:
    slug: str
    priority: int                       # higher wins on overlap
    patterns: list[re.Pattern]
    anti: list[re.Pattern] = field(default_factory=list)
    generic: set[str] = field(default_factory=set)  # pattern strings that anti blocks


def _rx(*words: str) -> list[re.Pattern]:
    return [re.compile(w, re.I | re.U) for w in words]


RULES: list[ThreatRule] = [
    # --- ballistic family (specific first) ---
    ThreatRule("kinzhal", 99, _rx(
        r"\bкинджал", r"\bкинжал", r"\bх[\s-]?47\b", r"\bkh[\s-]?47\b",
    )),
    ThreatRule("iskander", 98, _rx(
        r"\bискандер", r"\biskander", r"\bкн[\s-]?23\b", r"\bkn[\s-]?23\b",
        r"\b9м72", r"\bкндр\b",
    ), anti=_rx(r"искандер[\s-]*к\b")),
    ThreatRule("ballistic", 95, _rx(
        r"\bбал+истик", r"\bбал+истич", r"\b[аэ][эе]?робал+истич",
        r"\bс[\s-]?300\b[^.]*\b(пуск|ракет|удар|загроз)", r"\bс[\s-]?400\b[^.]*\bпуск",
        r"\bбр[\s-]?загроз", r"загроза\s+заст\w*\s+балист",
    )),
    # --- cruise family (specific first) ---
    ThreatRule("banderol", 92, _rx(
        r"\bбандерол", r"\bмгкр\b", r"\bs[\s-]?8000\b",
    )),
    ThreatRule("x22", 92, _rx(
        r"\bх[\s-]?22\b", r"\bх[\s-]?32\b", r"\bкх[\s-]?22\b", r"\bkh[\s-]?22\b",
    )),
    ThreatRule("x101", 91, _rx(
        r"\bх[\s-]?101\b", r"\bх[\s-]?55\b", r"\bх[\s-]?555\b",
        r"\bx[\s-]?101\b", r"\bx[\s-]?55\b",
    )),
    ThreatRule("kalibr", 91, _rx(
        r"\bкалибр", r"\bkalibr", r"\b3м[\s-]?14\b", r"\bкалибри\b",
    )),
    ThreatRule("cruise_missile", 88, _rx(
        r"\bкрилат", r"\bкрылат", r"\bкр\b",
        r"\bх[\s-]?59\b", r"\bх[\s-]?69\b", r"\bх[\s-]?35\b", r"\bx[\s-]?59\b",
        r"\bоникс", r"\bциркон", r"\bискандер[\s-]*к\b",
        r"\bкрилатих\b", r"\bракет[аи]\b",
    )),
    ThreatRule("kab", 90, _rx(
        r"\bкаб(?:а|и|ив|ов|ами|у|ом|iв)?\b", r"\bумпк\b", r"\bумпб\b",
        r"керован\w*\s+ав[иі]а", r"\bфаб[\s-]?\d", r"\bд[\s-]?30\b",
        r"глаид[\s-]?бомб", r"\bав[иі]абомб",
    ), anti=_rx(r"\bкабел", r"\bкабин")),
    ThreatRule("aircraft", 80, _rx(
        r"тактичн\w*\s+ав[иі]ац", r"тактическ\w*\s+ав[иі]ац",
        r"стратег[иі]чн\w*\s+ав[иі]ац",
        r"\bм[иі]г[\s-]?31\b", r"\bmig[\s-]?31\b",
        r"\bту[\s-]?95\b", r"\bту[\s-]?160\b", r"\bту[\s-]?22\b", r"\btu[\s-]?95\b",
        r"\bзл[иі]т\w*\s+\w*ав[иі]ац", r"\bвзлет\w*\s+\w*ав[иі]ац",
        r"\bборт[иі]в\b", r"\bбортов\b", r"\bносив\b.*ракет",
    )),
    ThreatRule("recon_uav", 70, _rx(
        r"розв[иі]дувальн\w*\s+(бпла|дрон|ц[иі]л)",
        r"разведыват\w*\s+(бпла|дрон|цел)",
        r"\bрозв[иі]дник", r"\bразведчик", r"\bорлан\b", r"\bzala\b", r"\bзала\b",
        r"supercam", r"суперкам", r"\bмерл[иі]н", r"\bфур[иі]я", r"\bлелека",
    )),
    ThreatRule(
        "jet_uav", 60,
        _rx(r"\bреактив", r"\bреакт[иі]вн", r"\bjet\b", r"\bджет\b",
            r"шахед[\s-]*238", r"\bs[\s-]?238\b"),
        anti=_rx(r"нереактив"),
        generic=set(),
    ),
    ThreatRule("shahed", 40, _rx(
        r"\bшахед", r"\bшахид", r"\bшахд\b", r"\bshahed",
        r"\bгерань", r"\bгеран\b", r"\bгерани", r"\bgeran",
        r"\bмопед", r"\bmoped\b", r"\bмоп\b",
        r"\bбпла\b", r"\bббпла\b", r"\bуав\b", r"\buav\b",
        r"\bдрон", r"\bбезп[иі]лотник", r"\bбеспилотник",
        r"\bкам[иі]кадзе", r"\bнереактивн", r"\bнереакт[иі]вн",
        r"\bптах\w*\s+ворог",
    )),
]

# last-resort net: clearly airborne, class unnamed
_MOTION_RX = _rx(
    r"\bкурс\w*\s+на\b", r"\bкурс\w*\s+в\b", r"\bу\s+напрямку\b", r"\bв\s+напрям",
    r"\bнапрямок\b", r"\bрухаетс", r"\bлетить\b", r"\bлетять\b", r"\bлетят\b",
    r"\bпролет", r"\bп[иі]дл[иі]т", r"\bподлет", r"\bповз\b", r"\bдовкол",
    r"\bв\s+б[иі]к\b", r"\bв\s+сторон", r"\bна\s+м[иі]сто\b", r"\bц[иі]л[ье]\b",
)


@dataclass(slots=True)
class ThreatMatch:
    slug: str
    raw: str
    start: int


def classify_line(folded_line: str) -> ThreatMatch | None:
    """Best threat match in a *folded* line, or None."""
    best: ThreatMatch | None = None
    best_priority = -1
    for rule in RULES:
        blocked = bool(rule.anti) and any(a.search(folded_line) for a in rule.anti)
        for p in rule.patterns:
            m = p.search(folded_line)
            if not m:
                continue
            if blocked:
                continue
            if rule.priority > best_priority:
                best = ThreatMatch(rule.slug, m.group(0).strip(), m.start())
                best_priority = rule.priority
            break
    if best is not None:
        return best
    if any(p.search(folded_line) for p in _MOTION_RX):
        return ThreatMatch("unknown", "", 0)
    return None
