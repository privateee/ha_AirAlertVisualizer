"""Work out, for a single line, which place is the object's current position
and which is where it is heading, then derive a compass heading.

Everything runs on the *folded* line (see ``normalize.fold``). We look at the
short window of text immediately before each toponym hit and classify the
preposition there into a role.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..geo.gazetteer import PlaceHit
from ..geo.util import bearing_deg

_WINDOW = 26  # chars of context before a toponym we inspect for a preposition
_SLASH_ONLY_RE = re.compile(r"\s*/\s*")   # "Добрянку / Любеч": just a list separator

# role -> ordered regexes matched in the pre-toponym window (closest wins)
_MARKERS: list[tuple[str, list[re.Pattern]]] = [
    ("dest", [re.compile(p, re.I) for p in (
        r"курс\w*\s+(?:на|в|у)\s*$",
        r"курс\w*\s*$",                       # "курс Теплик", "далі курс Троєщина"
        r"(?:у|в)\s+напрям\w*\s*(?:на|до)?\s*$",
        r"напрямок\s*$",
        r"[ву]\s+б[иі]к\s*$",
        r"[ву]\s+сторон\w*\s*$",
        r"рухаетс\w*\s+(?:на|в|у|до)\s*$",
        r"(?:летить|летять|летят|л[еі]тит)\s+(?:на|в|до)\s*$",
        r"(?:прямуе|иде|йде|п[іи]шов|выходит|заходит|зайшов)\s+(?:на|в|до)\s*$",
        r"дал\w*\s+(?:в\s+сторон\w*|на)\s*$",
        r"\bдал[иі]\s*$",                     # "2х Циркони далі Київ"
        r"\bна\s*$",
        r"\bдо\s*$",
        r"\bк\s*$",
    )]),
    ("src", [re.compile(p, re.I) for p in (
        r"\b[зи]\s+боку\s*$",
        r"\b[зи]\s+сторони\s*$",
        r"\bсо\s+стороны\s*$",
        r"\bз\s+напрямку\s*$",
        r"запуск\w*\s+[зи]\s*$",
        r"\bпуск\w*\s+[зи]\s*$",
        r"\b[зві]д\s*$",
        r"\bвід\s*$",
        r"\bз\s*$",
        r"\bзи\s*$",
        r"\bиз\s*$",
        r"\bот\s*$",
    )]),
    ("circle", [re.compile(p, re.I) for p in (
        r"довкол\w*\s*$",
        r"кружл\w*\s*$",
        r"кружит\s*$",
        r"барраж\w*\s*$",
        r"нар[еі]за\w*\s+круг\w*\s*$",
    )]),
    ("transit", [re.compile(p, re.I) for p in (
        r"\bповз\s*$",
        r"\bм[иі]мо\s*$",
        r"\bнад\s*$",
        r"\bчерез\s*$",
        r"пролет\w*\s*$",
        r"проли?та\w*\s*$",
        r"пройш\w*\s*$",
        r"\bв\s+р[- ]?н[іи]?\.?\s*$",
        r"\bр[- ]?н\.?\s*$",
        r"\b(?:в|у)\s+ра[иі]он\w*\s*$",
        r"\bра[иі]он\s*$",
        r"\bпоблизу\s*$",
        r"\bкол[оя]\s*$",
        r"\bб[іи]ля\s*$",
    )]),
]

_LAUNCH_RX = re.compile(r"\b(пуск\w*|запуск\w*|старт\w*|зл[іи]т\w*|взлет\w*|"
                        r"вих[іи]д\w*|виход\w*|выход\w*)\b", re.I)   # "Вихід Циркону Курськ"
# "🔻Зниження Либідська", "Голосіїв зниження", "снижается" - losing height,
# often right before a strike, so worth its own status
# "падає", "впаде на ..." - coming down, about to hit
_DESCENT_RX = re.compile(r"\b(знижен\w*|знижу\w*|снижен\w*|снижа\w*|п[іи]к[іи]ру\w*|"
                         r"пада\w*|впаде\w*|упад[еу]\w*|"
                         # "Київ — спуск балістики!", "Корчувате. Спускається."
                         r"спуск\w*|спуска\w*)\b", re.I)
# the text is folded (і -> и), so "приліт" arrives as "прилит"; slang:
# "жахнуло", "влупило", "бахнуло", "гахнуло"
_IMPACT_RX = re.compile(r"\b(вибух\w*|взрыв\w*|прил[ьеіи]т\w*|прил[оіи]т\w*|детонац\w*|"
                        r"уражен\w*|работа\s+пво|збит\w*|сбит\w*|"
                        r"жахн\w*|влуп\w*|бахн\w*|гахн\w*)\b", re.I)
_CLEAR_RX = re.compile(
    r"\b(чисто|в[іи]дб[іи]й\w*|отб[оі]й\w*|минув\w*|минул[аои]\w*|прол[еі]т[еі]л\w*\s+повз|"
    r"пройшл\w*\s+повз|загроз\w*\s+мин\w*|небо\s+чист\w*|все\s+тих\w*|"
    # "Мінус" / "минуса" - the target is down (shot down / gone)
    r"минус\w*)\b", re.I,
)


def is_clear(folded_line: str) -> bool:
    """True if the line is an 'all clear' / threat-passed statement."""
    return bool(_CLEAR_RX.search(folded_line))

# "з півночі", "со стороны юга", "із північного заходу" -> the compass point
# the object is coming FROM. Heading is the opposite (+180). Folded alphabet.
_CARDINAL: list[tuple[re.Pattern, float]] = [
    (re.compile(r"п[іи]вн[іи]чн\w*[\s-]*сх[іо]д|сев\w*[\s-]*вост\w*|п[іи]вн[іи]чно[\s-]*сх[іи]дн", re.I), 45),
    (re.compile(r"п[іи]вн[іи]чн\w*[\s-]*зах[іо]д|сев\w*[\s-]*запад\w*|п[іи]вн[іи]чно[\s-]*зах[іи]дн", re.I), 315),
    (re.compile(r"п[іи]вденн\w*[\s-]*сх[іо]д|юго[\s-]*вост\w*|п[іи]вденно[\s-]*сх[іи]дн", re.I), 135),
    (re.compile(r"п[іи]вденн\w*[\s-]*зах[іо]д|юго[\s-]*запад\w*|п[іи]вденно[\s-]*зах[іи]дн", re.I), 225),
    (re.compile(r"\bп[іи]вноч[іи]\b|\bп[іи]вн[іи]чи\b|\bсевера\b|\bс севера\b", re.I), 0),
    (re.compile(r"\bп[іи]вдн\w*\b|\bюга\b|\bс юга\b", re.I), 180),
    (re.compile(r"\bсход[уі]\b|\bзсходу\b|\b[зи]?[іи]?\s*сходу\b|\bвостока\b", re.I), 90),
    (re.compile(r"\bзаходу\b|\b[зи]з?\s*заходу\b|\bзапада\b", re.I), 270),
]
_REGION_SUFFIX = ("щина", "щини", "щину", "щино", "области", "област", "обл")


def _from_cardinal(folded_line: str) -> float | None:
    """Heading implied by a 'coming from <compass>' phrase, or None."""
    if not re.search(
        r"\b(?:з|зи|зо|с|со|из|вид|von|from)\b[^.]{0,18}"
        r"(п[іи]вн|п[іи]вд|сход|заход|сев|юг|вост|запад)",
        folded_line,
    ):
        return None
    for rx, frm in _CARDINAL:
        if rx.search(folded_line):
            return (frm + 180.0) % 360.0
    return None


# "курс західний/южный/..." names the heading directly (unlike "coming from
# <compass>" above, which is the opposite of the stated point) - so these map
# straight to the compass bearing, no +180 flip. Intercardinals first so
# "курс північно-східний" doesn't get caught by the bare "північний" pattern.
_COURSE_WORD: list[tuple[re.Pattern, float]] = [
    (re.compile(r"курс\w*\s+п[іи]вн[іи]чно[\s-]*сх[іи]дн\w*|курс\w*\s+сев\w*[\s-]*вост\w*", re.I), 45),
    (re.compile(r"курс\w*\s+п[іи]вн[іи]чно[\s-]*зах[іи]дн\w*|курс\w*\s+сев\w*[\s-]*запад\w*", re.I), 315),
    (re.compile(r"курс\w*\s+п[іи]вденно[\s-]*сх[іи]дн\w*|курс\w*\s+юго[\s-]*вост\w*", re.I), 135),
    (re.compile(r"курс\w*\s+п[іи]вденно[\s-]*зах[іи]дн\w*|курс\w*\s+юго[\s-]*запад\w*", re.I), 225),
    (re.compile(r"курс\w*\s+п[іи]вн[іи]чн\w*|курс\w*\s+север\w*", re.I), 0),
    (re.compile(r"курс\w*\s+п[іи]вденн\w*|курс\w*\s+южн\w*", re.I), 180),
    (re.compile(r"курс\w*\s+сх[іи]дн\w*|курс\w*\s+восточ\w*", re.I), 90),
    (re.compile(r"курс\w*\s+зах[іи]дн\w*|курс\w*\s+запад\w*", re.I), 270),
]


# "у північно-західному напрямку", "➡️ у західному напрямку" (kpszsu) -
# also the heading itself, not where it comes from
_DIR_WORD: list[tuple[re.Pattern, float]] = [
    (re.compile(rf"\b(?:у|в)\s+{adj}\w*\s+напрям", re.I), deg) for adj, deg in (
        (r"п[іи]вн[іи]чно[\s-]*сх[іи]дн", 45), (r"п[іи]вн[іи]чно[\s-]*зах[іи]дн", 315),
        (r"п[іи]вденно[\s-]*сх[іи]дн", 135), (r"п[іи]вденно[\s-]*зах[іи]дн", 225),
        (r"п[іи]вн[іи]чн", 0), (r"п[іи]вденн", 180), (r"сх[іи]дн", 90), (r"зах[іи]дн", 270),
        (r"северо[\s-]*восточн", 45), (r"северо[\s-]*западн", 315),
        (r"юго[\s-]*восточн", 135), (r"юго[\s-]*западн", 225),
        (r"северн", 0), (r"южн", 180), (r"восточн", 90), (r"западн", 270),
    )
]


def _from_course_word(folded_line: str) -> float | None:
    """Heading from a bare 'курс <cardinal adjective>' or 'у <cardinal> напрямку'
    statement, or None."""
    for rx, deg in (*_COURSE_WORD, *_DIR_WORD):
        if rx.search(folded_line):
            return deg
    return None


def _is_region(hit: PlaceHit | None) -> bool:
    return bool(hit) and hit.text.strip().endswith(_REGION_SUFFIX)


@dataclass(slots=True)
class DirectionResult:
    primary: PlaceHit | None
    dest: PlaceHit | None
    src: PlaceHit | None
    heading_deg: float | None
    status: str


def _role_for(folded_line: str, hit: PlaceHit) -> str:
    window = folded_line[max(0, hit.start - _WINDOW): hit.start]
    best_role = "near"
    best_end = -1
    for role, patterns in _MARKERS:
        for rx in patterns:
            m = rx.search(window)
            if m and m.end() >= best_end:
                best_end = m.end()
                best_role = role
    return best_role


def analyze(folded_line: str, hits: list[PlaceHit]) -> DirectionResult:
    roles = [(h, _role_for(folded_line, h)) for h in hits]
    # "на Добрянку / Любеч / Неданчичі" (horizon_of_war): a route - a place
    # listed after "/" shares the role of the one before it. It used to be
    # "near" (= where it is now), so the route read backwards: at Любеч,
    # heading for Добрянка.
    roles.sort(key=lambda hr: hr[0].start)
    for i in range(1, len(roles)):
        (prev, prole), (h, role) = roles[i - 1], roles[i]
        if (role == "near" and prole in ("dest", "transit")
                and _SLASH_ONLY_RE.fullmatch(folded_line[prev.end:h.start])):
            roles[i] = (h, "dest" if prole == "dest" else "transit")

    dests = [h for h, r in roles if r == "dest"]
    src = next((h for h, r in roles if r == "src"), None)
    transit = next((h for h, r in roles if r in ("transit", "circle")), None)
    circling = any(r == "circle" for _, r in roles)
    near = next((h for h, r in roles if r == "near"), None)

    dest = dests[-1] if dests else None
    src_is_region = _is_region(src)

    # Where is the object *now*?
    #  - an over/near/past place always wins
    #  - "from <specific place> [to <dest>]"  -> it is right there by the place
    #    ("з київського водосховища на київ" -> position = the reservoir)
    #  - "from <oblast/region> to <dest>"     -> region is only a direction,
    #    so the best known position is the destination
    #  - several destinations                 -> first is the current leg
    #  - single destination                   -> leave None for the header
    primary = transit or near
    if primary is None:
        if src is not None and not src_is_region:
            primary = src
        elif src is not None and dest is not None:
            primary = dest          # region source is only a direction
        elif src is not None:
            primary = src
        elif len(dests) >= 2:
            primary = dests[0]
            dest = dests[-1]
        # a lone destination with no source stays None: the caller falls back
        # to the post's oblast header, or demotes the destination itself

    def _same(a: PlaceHit | None, b: PlaceHit | None) -> bool:
        return a is not None and b is not None and a.place.name == b.place.name

    if _same(dest, primary):
        dest = None
    if _same(src, primary):
        src = None

    # heading: follow the src -> position -> dest chain; a cardinal phrase
    # ("з півночі") overrides, since it is stated explicitly.
    chain: list[PlaceHit] = []
    for h in (src, primary, dest):
        if h is not None and (not chain or not _same(chain[-1], h)):
            chain.append(h)
    heading: float | None = None
    if len(chain) >= 2:
        heading = bearing_deg(chain[-2].place.coord, chain[-1].place.coord)
    cardinal = _from_cardinal(folded_line)
    if cardinal is None:
        cardinal = _from_course_word(folded_line)
    if cardinal is not None:
        heading = cardinal

    moved = any(r in ("transit", "circle") for _, r in roles)
    if circling:
        status = "circling"
    elif _LAUNCH_RX.search(folded_line):
        status = "launch"
    elif _IMPACT_RX.search(folded_line):
        status = "impact"
    elif _DESCENT_RX.search(folded_line):
        status = "descending"
    elif _CLEAR_RX.search(folded_line):
        status = "clear"
    elif heading is not None or dest is not None or moved:
        status = "moving"
    else:
        status = "unknown"

    return DirectionResult(primary, dest, src, heading, status)
