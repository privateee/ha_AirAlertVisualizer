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
    # adjectives only: "Запоріжжя" / "у Запоріжжі" is the city, and blocking
    # every "запориж..." key used to make the city unmatchable
    (r"херсонщин", "kherson"), (r"запоризк|запорожск|запорижчин", "zapor"),
    (r"донеччин", "donetsk"), (r"луганщин", "luhansk"),
    (r"ривненщин|ровенщин", "rivne"), (r"\bволин", "volyn"),
    (r"тернопильщин", "ternopil"), (r"львивщин", "lviv"),
    (r"ивано[\s-]?франкивщин|прикарпатт", "if"),
    (r"закарпатт", "zakarp"), (r"буковин|чернивеччин", "chernivtsi"),
    (r"брянщин|брянськ|брянско", "ru_bryansk"),
    (r"курщин|курськ|курско", "ru_kursk"),
    (r"белгородщин|белгородск", "ru_belgorod"),
    (r"гомельщин|гомельск", "by_homel"),
    # launch areas that are only ever a direction of approach
    (r"\bкрим\w{0,3}\b|\bкрым\w{0,2}\b|\bкримськ", "crimea"),
    (r"чорн\w{0,3}\s+мор|черн\w{0,3}\s+мор", "black_sea"),
    (r"азовськ\w{0,3}\s+мор|азовск\w{0,3}\s+мор|\bазов\w{0,2}\b", "azov_sea"),
    (r"\bкаспи\w{0,4}", "caspian"),
    (r"\bбилорус\w{0,4}|\bбеларус\w{0,4}|\bрб\b", "belarus"),
]
# "<Сумськ>ій області", "Полтавскую область", "Київської обл." - oblast
# adjectives (uk + ru), stem -> oblast key
_OBLAST_ADJ_STEMS: list[tuple[str, str]] = [
    ("киив|киев", "kyiv_o"), ("харкив|харьков", "kharkiv"), ("полтав", "poltava"),
    ("днипропетров|днепропетров", "dnipro"), ("черкас", "cherkasy"),
    ("черниг[иі]в|чернигов", "chernihiv"), ("сум", "sumy"), ("житомир", "zhytomyr"),
    ("винниц|винниц", "vinnytsia"), ("хмельниц", "khmeln"),
    ("кировоград|кропивниц", "kropyv"), ("миколаив|николаев", "mykolaiv"),
    ("одес", "odesa"), ("херсон", "kherson"), ("запориз|запорож", "zapor"),
    ("донец", "donetsk"), ("луган", "luhansk"), ("ривнен|ровен", "rivne"),
    ("волин|волын", "volyn"), ("тернопил|тернопол", "ternopil"), ("львив|львов", "lviv"),
    (r"ивано[\s-]?франкив|ивано[\s-]?франков", "if"), ("закарпат", "zakarp"),
    ("чернивец|черновиц", "chernivtsi"), ("брян", "ru_bryansk"), ("кур", "ru_kursk"),
    ("белгород", "ru_belgorod"),
]
_REGION_WORDS += [
    (rf"\b(?:{stem})\w{{0,7}}\s+обл", key)
    for stem, key in _OBLAST_ADJ_STEMS
]
_REGION_RE = [(re.compile(p, re.I), k) for p, k in _REGION_WORDS]
# centres for region keys no gazetteer place belongs to
_EXTRA_CENTRES = {
    "crimea": (45.3, 34.4), "black_sea": (45.0, 31.5), "azov_sea": (46.2, 36.5),
    "caspian": (42.5, 50.0), "belarus": (52.5, 28.0),
}
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
    "crimea": "Крим", "black_sea": "Чорне море", "azov_sea": "Азовське море",
    "caspian": "Каспійське море", "belarus": "Білорусь",
}


# "у Миколаївському районі", "Броварський р-н", "Белоцерковском районе": a
# district (raion) named after its centre town - the town is the best position
# we have. Runs on folded text, where "-ськ-/-цьк-" are "-ск-/-цк-".
_RAION_RE = re.compile(
    r"(?<![а-яёa-z0-9\-])([а-я\-]{3,}?)(ск|цк|зк)"
    r"(?:ому|ого|ий|им|ии|ом|ои|ая|ую|и|е)?\s+(?:район\w*|р-н\w*|р\.\s?н\.?)",
    re.I)
_OBLAST_AFTER_RE = re.compile(r"\s+облас|\s+обл\b")
# adjective stems whose town name isn't derivable by the rules in _raion_keys
_RAION_IRREGULAR = {
    "билоцеркив": "била церква", "белоцерков": "белая церковь",
    "ромен": "ромни", "золотони": "золотоноша",
}

# Typos: one wrong key on a Ukrainian / Russian ЙЦУКЕН keyboard, one extra
# letter, or two letters swapped ("Ьориспіль", "Святощин", "Виноградао").
# Deliberately NOT a general fuzzy match: "Лісники" is one letter from a
# different real village ("Лиснівка"), so a *missing* letter is never guessed.
_KEY_ROWS = ("йцукенгшщзхї", "фівапролджє", "ячсмитьбю",
             "йцукенгшщзхъ", "фывапролджэ", "ячсмитьбю")


def _key_neighbours() -> dict[str, set[str]]:
    nb: dict[str, set[str]] = {}
    for layout in (_KEY_ROWS[:3], _KEY_ROWS[3:]):
        for r, row in enumerate(layout):
            for i, ch in enumerate(row):
                near = nb.setdefault(ch, set())
                for rr in (r - 1, r, r + 1):
                    if 0 <= rr < len(layout):
                        for j in (i - 1, i, i + 1):
                            if 0 <= j < len(layout[rr]) and layout[rr][j] != ch:
                                near.add(layout[rr][j])
    return nb


_NEIGHBOURS = _key_neighbours()
_CAP_WORD_RE = re.compile(r"(?<![А-Яа-яЁёІіЇїЄєҐґ'’ʼ])[А-ЯЁІЇЄҐЬ][а-яёіїєґь'’ʼ]{4,}")


def _typo_variants(word: str) -> set[str]:
    out: set[str] = set()
    for i, ch in enumerate(word):
        out.add(word[:i] + word[i + 1:])                      # an extra letter
        for n in _NEIGHBOURS.get(ch, ()):
            out.add(word[:i] + n + word[i + 1:])              # a neighbouring key
        if i + 1 < len(word):
            out.add(word[:i] + word[i + 1] + ch + word[i + 2:])  # swapped pair
    out.discard(word)
    return out


def _raion_keys(stem: str, suffix: str) -> list[str]:
    """Town-name candidates for a district adjective, most likely first."""
    if stem in _RAION_IRREGULAR:
        return [_RAION_IRREGULAR[stem]]
    keys = [stem, stem + suffix, stem + suffix + "ий", stem + "а", stem + "е",
            stem + "и", stem + "ка", stem + "я", stem + "о"]
    if suffix == "цк":
        keys.insert(1, stem + "к")            # Кременчуцький -> Кременчук
    if stem.endswith("в"):
        keys.append(stem[:-1])                # Дніпровський -> Дніпро
    if stem.endswith("ин"):
        keys.append(stem[:-2] + "а")          # Шосткинський -> Шостка
    if stem.endswith("ив"):
        keys.append(stem[:-2] + "ове")        # Синельниківський -> Синельникове
    return keys


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
    # plural town names: "з Черкас", "біля Прилук", "Петрівців", "у Черкасах"
    if len(last) >= 6 and last[-1] == "и":
        stem = last[:-1]
        forms = {last, stem, stem + "ах", stem + "ам"}
        if stem.endswith("ц"):
            forms.add(stem + "ив")
        return {head + x for x in forms}
    # >=4, not >=5: short vowel-ending names ("Мена" -> "мени"/"мену") need
    # this too, not just longer ones ("Троєщина").
    if len(last) < 4 or last[-1] not in "аяое":
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
        centre = self._obl_center.get(obl) or _EXTRA_CENTRES.get(obl, (50.45, 30.52))
        return _OBLAST_LABEL.get(obl, obl), centre

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
            # "південне" (a real village) declines to "південні", and the
            # case-ending wildcard below would then swallow "ше" and steal
            # every "південніше <real place>" line ("північніше"/"західніше"
            # /"східніше" - same shape). A suffix starting with "ш" right
            # after an -і/-и ending is the Ukrainian comparative degree
            # ("-іший"/"-іше"), never a genuine case ending.
            swallowed = folded_text[m.end(1): m.end(0)]
            if key[-1:] in "іи" and swallowed[:1] == "ш":
                continue
            # "Черкаська область", "Київської обл." - an oblast adjective (the
            # region matcher owns those), not the town the key belongs to
            if swallowed and _OBLAST_AFTER_RE.match(folded_text, m.end()):
                continue
            span = (m.start(), m.end())
            if any(s <= span[0] < e for s, e in seen_spans):
                continue
            seen_spans.append(span)
            place = self._pick(list(cands), oblast_hint, area_center)
            hits.append(PlaceHit(place, span[0], span[1], m.group(0)))

        for m in _RAION_RE.finditer(folded_text):
            if any(s <= m.start() < e or m.start() <= s < m.end() for s, e in seen_spans):
                continue
            for key in _raion_keys(m.group(1).lower(), m.group(2).lower()):
                cands = self._by_key.get(key)
                if cands:
                    place = self._pick(list(cands), oblast_hint, area_center)
                    seen_spans.append((m.start(), m.end()))
                    hits.append(PlaceHit(place, m.start(), m.end(), m.group(0)))
                    break
        hits.sort(key=lambda h: h.start)
        return hits

    def typo_hits(
        self, line: str, folded_line: str, *,
        area_center: tuple[float, float] | None = None,
    ) -> list[PlaceHit]:
        """Places for capitalised words in ``line`` (as written) that are a
        keyboard slip away from a known name. For lines where ``find`` found
        nothing; positions refer to ``folded_line``."""
        hits: list[PlaceHit] = []
        for m in _CAP_WORD_RE.finditer(line):
            word = m.group(0)
            place = self.typo_place(word, area_center=area_center)
            if place is None:
                continue
            fw = fold(word)
            at = folded_line.find(fw) if fw else -1
            if at >= 0:
                hits.append(PlaceHit(place, at, at + len(fw), fw))
        return hits

    def typo_place(
        self, word: str, *, area_center: tuple[float, float] | None = None,
        max_km: float = 200.0,
    ) -> Place | None:
        """The place a mistyped name most likely means, or None. ``word`` is
        the token as written (lower-cased, *not* folded - "ь" for "б" is a
        typo that folding would erase). Only accepted when every plausible
        reading points to the same place, near the area when one is given."""
        word = word.lower()
        if len(word) < 5:
            return None
        found: dict[str, Place] = {}
        for v in _typo_variants(word):
            fv = fold(v)
            m = self._scan_re.match(fv)
            if not m or m.end() != len(fv):
                continue
            cands = self._by_key.get(fold(m.group(1)))
            if not cands:
                continue
            p = self._pick(list(cands), None, area_center)
            found[p.name] = p
        if len(found) != 1:
            return None
        place = next(iter(found.values()))
        if area_center is not None and haversine_km(place.coord, area_center) > max_km:
            return None
        return place

    def lookup(self, text: str, **kw) -> PlaceHit | None:
        hits = self.find(fold(text), **kw)
        return hits[0] if hits else None
