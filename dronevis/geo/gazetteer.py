"""Load the seed gazetteer and find/resolve toponyms in folded text.

Matching strategy
-----------------
Every place contributes one or more *folded* match strings (name + aliases,
parentheticals stripped). Each becomes a regex fragment
``<literal>[а-яёʼ'\\-]*`` so Ukrainian/Russian case endings still match
("борисполь" catches "борисполя", "борисполі"). All fragments join into one
alternation, longest first, with a left word boundary.

When one folded string maps to several places (e.g. two "Семенівка"), the
resolver picks by: oblast hint from the post header -> proximity to the active
area centre -> higher rank -> first.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..parse.normalize import fold
from .util import haversine_km

_DATA = Path(__file__).parent / "data" / "gazetteer.json"
_PAREN_RE = re.compile(r"\s*\([^)]*\)")
# Only inflectional endings (борисполь -> борисполя/борисполю), and the word
# must then end: "днипро" must not match inside "днипропетровщина".
_SUFFIX = r"[а-яёʼ'\-]{0,3}(?![а-яёa-z])"

# Region words ("Київщина", "на Харківщині", "Одещина") -> oblast key. Matched
# independently of the place list so they always resolve to the oblast, never
# to the same-named city.
# Patterns run against *folded* text (і/ї/ы -> и, є -> е), so spell them with и.
_REGION_WORDS: list[tuple[str, str]] = [
    (r"киивщин", "kyiv_o"), (r"харкивщин", "kharkiv"),
    (r"полтавщин", "poltava"), (r"днипропетровщин|днипрощин", "dnipro"),
    (r"черкащин", "cherkasy"), (r"чернигивщин", "chernihiv"),
    (r"сумщин", "sumy"), (r"житомирщин", "zhytomyr"),
    (r"винниччин", "vinnytsia"), (r"хмельниччин", "khmeln"),
    (r"кировоградщин|кропивниччин", "kropyv"),
    (r"миколаивщин|николаевщин", "mykolaiv"), (r"одещин", "odesa"),
    (r"херсонщин", "kherson"), (r"запориж|запориз", "zapor"),
    (r"донеччин", "donetsk"), (r"луганщин", "luhansk"),
    (r"ривненщин|ровенщин", "rivne"), (r"\bволин", "volyn"),
    (r"тернопильщин", "ternopil"), (r"львивщин", "lviv"),
    (r"ивано[\s-]?франкивщин|прикарпатт", "if"),
    (r"закарпатт", "zakarp"), (r"буковин|чернивеччин", "chernivtsi"),
    (r"брянщин|брянськ|брянско", "ru_bryansk"),
    (r"курщин|курськ|курско", "ru_kursk"),
    (r"белгородщин|белгородск", "ru_belgorod"),
    (r"гомельщин|гомельск", "by_homel"),
]
_REGION_RE = [(re.compile(p, re.I), k) for p, k in _REGION_WORDS]
# a suffix like "...щина / ...ччина / ... область" turns a city name into a
# region name ("Київ" -> "Київщина")
_REGION_TAIL_RE = re.compile(r"(щин|ччин|облас)", re.I)

_OBLAST_LABEL = {
    "kyiv_c": "Київ", "kyiv_o": "Київська обл.", "kharkiv": "Харківська обл.",
    "poltava": "Полтавська обл.", "dnipro": "Дніпропетровська обл.",
    "cherkasy": "Черкаська обл.", "chernihiv": "Чернігівська обл.",
    "sumy": "Сумська обл.", "zhytomyr": "Житомирська обл.",
    "vinnytsia": "Вінницька обл.", "khmeln": "Хмельницька обл.",
    "kropyv": "Кіровоградська обл.", "mykolaiv": "Миколаївська обл.",
    "odesa": "Одеська обл.", "kherson": "Херсонська обл.",
    "zapor": "Запорізька обл.", "donetsk": "Донецька обл.",
    "luhansk": "Луганська обл.", "rivne": "Рівненська обл.",
    "volyn": "Волинська обл.", "ternopil": "Тернопільська обл.",
    "lviv": "Львівська обл.", "if": "Івано-Франківська обл.",
    "zakarp": "Закарпатська обл.", "chernivtsi": "Чернівецька обл.",
    "ru_bryansk": "Брянська обл. (рф)", "ru_kursk": "Курська обл. (рф)",
    "ru_belgorod": "Бєлгородська обл. (рф)", "by_homel": "Гомельська обл. (бр)",
}


@dataclass(slots=True)
class Place:
    name: str
    lat: float
    lon: float
    kind: str
    obl: str
    rank: int
    keys: tuple[str, ...] = ()

    @property
    def coord(self) -> tuple[float, float]:
        return (self.lat, self.lon)


@dataclass(slots=True)
class PlaceHit:
    place: Place
    start: int
    end: int
    text: str


def _decline(f: str) -> set[str]:
    """Ukrainian/Russian case forms of a folded toponym that ends in a vowel,
    so "Троєщина" also matches "на Троєщину", "Борщагівка" -> "повз Борщагівки".
    Only the last word is inflected. Spurious forms are harmless (they match
    nothing); we do NOT shorten to a bare stem (that would over-match)."""
    parts = f.rsplit(" ", 1)
    head, last = (parts[0] + " ", parts[1]) if len(parts) == 2 else ("", f)
    if len(last) < 5 or last[-1] not in "аяое":
        return {f}
    stem = last[:-1]
    forms = {last, stem + "и", stem + "у", stem + "і", stem + "е"}
    if last.endswith("я"):
        forms |= {stem + "ю", stem + "ї"}
    if last.endswith("ка"):
        forms.add(stem[:-1] + "ці")
    elif last.endswith("га"):
        forms.add(stem[:-1] + "зі")
    elif last.endswith("ха"):
        forms.add(stem[:-1] + "сі")
    return {head + x for x in forms}


def _keys_for(name: str, aliases: list[str]) -> set[str]:
    out: set[str] = set()
    for raw in [name, *aliases]:
        f = fold(_PAREN_RE.sub("", raw)).strip()
        if len(f) < 3:
            continue
        # never index a *known oblast* word as a place key ("Харківщина" is an
        # oblast, not the city Харків) - but keep real settlements that happen
        # to end in -щина, like Крюківщина.
        if any(rx.search(f) for rx, _ in _REGION_RE):
            continue
        out |= _decline(f)
    return out


class Gazetteer:
    def __init__(self, path: str | Path | None = None):
        data = json.loads(Path(path or _DATA).read_text(encoding="utf-8"))
        self.places: list[Place] = []
        self._by_key: dict[str, list[Place]] = {}
        self._obl_center: dict[str, tuple[float, float]] = {}

        for row in data["places"]:
            keys = tuple(sorted(_keys_for(row["name"], row.get("aliases", []))))
            p = Place(
                name=row["name"], lat=float(row["lat"]), lon=float(row["lon"]),
                kind=row.get("kind", "place"), obl=row.get("obl", ""),
                rank=int(row.get("rank", 2)), keys=keys,
            )
            self.places.append(p)
            for k in keys:
                self._by_key.setdefault(k, []).append(p)
            # first rank-5 place in an oblast is that oblast's centre
            if p.rank >= 5 and p.obl not in self._obl_center:
                self._obl_center[p.obl] = p.coord

        all_keys = sorted(self._by_key, key=len, reverse=True)
        self._scan_re = re.compile(
            r"(?<![а-яёa-z0-9\-])(" +
            "|".join(re.escape(k).replace(r"\ ", r"\s+") for k in all_keys) +
            r")" + _SUFFIX,
            re.I | re.U,
        )

    # -- oblast helpers -------------------------------------------------------
    def oblast_center(self, obl: str) -> tuple[float, float] | None:
        return self._obl_center.get(obl)

    def detect_oblast(self, folded_text: str) -> str | None:
        """Oblast implied by a region word ('Полтавщина', 'на Харківщині',
        'Одещина'). Returns the earliest-appearing one, or None."""
        head = folded_text[:100]
        best_pos = len(head) + 1
        best_obl: str | None = None
        for rx, obl in _REGION_RE:
            m = rx.search(head)
            if m and m.start() < best_pos:
                best_pos = m.start()
                best_obl = obl
        return best_obl

    def region_hit(self, folded_text: str) -> tuple[str, tuple[float, float]] | None:
        """(label, centre) for a region word anywhere in the text - used as a
        *directional* origin, never as a precise position."""
        obl = self.detect_oblast(folded_text)
        if obl is None:
            return None
        return _OBLAST_LABEL.get(obl, obl), self._obl_center.get(obl, (50.45, 30.52))

    # -- resolution --------------------------------------------------------
    def _pick(
        self,
        candidates: list[Place],
        oblast_hint: str | None,
        area_center: tuple[float, float] | None,
    ) -> Place:
        if len(candidates) == 1:
            return candidates[0]
        if oblast_hint:
            same = [c for c in candidates if c.obl == oblast_hint]
            if same:
                candidates = same
        if len(candidates) == 1:
            return candidates[0]
        if area_center is not None:
            candidates = sorted(
                candidates, key=lambda c: haversine_km(c.coord, area_center)
            )
            near = candidates[0]
            if haversine_km(near.coord, area_center) < 250:
                return near
        return max(candidates, key=lambda c: c.rank)

    def find(
        self,
        folded_text: str,
        *,
        oblast_hint: str | None = None,
        area_center: tuple[float, float] | None = None,
    ) -> list[PlaceHit]:
        """All toponym hits in order of appearance (overlaps removed)."""
        hits: list[PlaceHit] = []
        seen_spans: list[tuple[int, int]] = []
        for m in self._scan_re.finditer(folded_text):
            key = fold(m.group(1))
            cands = self._by_key.get(key)
            if not cands:
                continue
            # "київщина" would otherwise match the "київ" key + a swallowed
            # suffix. A trailing region marker the key itself lacks means this
            # is an oblast reference - let detect_oblast / region_hit own it.
            tail = folded_text[m.end(1): m.end(1) + 5]
            if _REGION_TAIL_RE.match(tail) and not _REGION_TAIL_RE.search(key):
                continue
            span = (m.start(), m.end())
            if any(s <= span[0] < e for s, e in seen_spans):
                continue
            seen_spans.append(span)
            place = self._pick(list(cands), oblast_hint, area_center)
            hits.append(PlaceHit(place, span[0], span[1], m.group(0)))
        return hits

    def lookup(self, text: str, **kw) -> PlaceHit | None:
        hits = self.find(fold(text), **kw)
        return hits[0] if hits else None
