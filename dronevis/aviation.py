"""Strategic / missile-carrier aviation: a takeoff is a *timer*, not a place.

"Зліт МіГ-31К", "Ту-95МС в повітрі", "Ту-22М3 вилетіли" - the aircraft are
over Russia; what matters is how long they've been up, because launches
(Kinzhal, Kh-101, Kh-22) come some time after takeoff. A relocation between
airfields ("перебазування Ту-95 з Енгельса на Оленью") is NOT a threat and
never starts a timer. A landing or "відбій" for that type ends it; so does
the type's timeout, when nobody reports the end.

All matching runs on the *folded* text (normalize.fold: і/ї -> и, є -> е,
apostrophes and ь dropped, lower case).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from .parse.normalize import fold, strip_hints


@dataclass(frozen=True, slots=True)
class Group:
    key: str
    label: str
    rx: re.Pattern
    launch_min: tuple[int, int]    # usual launches this long after takeoff
    timeout_min: int               # alert ends on its own after this


GROUPS: tuple[Group, ...] = (
    Group("mig31", "МіГ-31К",
          re.compile(r"\bм[иі]г[\s-]*31|\bmig[\s-]*31|кинджал|кинжал"), (15, 40), 120),
    Group("tu22", "Ту-22М3",
          re.compile(r"\bту[\s-]*22|\btu[\s-]*22"), (60, 120), 300),
    Group("strategic", "Ту-95МС / Ту-160",
          re.compile(r"\bту[\s-]*95|\bту[\s-]*160|\b[ат]н[\s-]*160\b|\btu[\s-]*(?:95|160)"
                     r"|стратег\w*\s+ав[иі]ац|стратег\w*\s+борт"), (120, 240), 600),
)

# takeoff / airborne: the start of a threat
_UP_RX = re.compile(
    r"\bзл[иі]т\w*|\bзлет[иі]\w*|\bзлетил\w*|\bвзлет\w*|\bвилет\w*|\bвылет\w*"
    r"|[ву]\s+пов[иі]тр|в\s+воздух|\bп[иі]дня\w*|\bподня\w*|\bв\s+неб[иі]")
# relocation between airfields: never a threat
_RELOC_RX = re.compile(
    r"перебаз\w*|передислок\w*|перелет\w*|перел[иі]т\w*|перегон\w*|перекид\w*"
    r"|з\s+аеродром\w*\s+\w+\s+на\s+аеродром|на\s+аеродром\w*\s+(?:базуван|постийн)")
# landing / all clear for that group ("сіли" is left out: folded it's "сили",
# as in "Повітряні сили")
_DOWN_RX = re.compile(r"\bпосадк\w*|\bприземл\w*|\bвидбий\b|\bотбой\b"
                      # a channel calling it off: "скасовано", "не підтверджується"
                      r"|скасова\w*|\bвидмин\w*|\bотмен\w*|не\s+п[иі]дтвердж\w*"
                      r"|не\s+подтвержд\w*|хибн\w*|ложн\w*")


@dataclass(slots=True)
class Mention:
    group: str
    kind: str          # "up" | "reloc" | "down"


def classify(text: str) -> list[Mention]:
    """What a post says about missile-carrier aircraft, per group."""
    f = strip_hints(fold(text or ""))
    out: list[Mention] = []
    for g in GROUPS:
        if not g.rx.search(f):
            continue
        if _RELOC_RX.search(f):
            out.append(Mention(g.key, "reloc"))
        elif _DOWN_RX.search(f):
            out.append(Mention(g.key, "down"))
        elif _UP_RX.search(f):
            out.append(Mention(g.key, "up"))
    return out


def active_alerts(posts, now: datetime) -> list[dict]:
    """``posts``: (posted_at datetime, channel, url, text) oldest first.
    One alert per group whose latest takeoff isn't followed by a landing, an
    all clear / cancellation, or a channel saying it's a relocation - and
    hasn't timed out. Relocations are reported separately,
    never as alerts."""
    by_key = {g.key: g for g in GROUPS}
    state: dict[str, dict] = {}
    relocs: dict[str, dict] = {}
    for t, channel, url, text in posts:
        for m in classify(text):
            g = by_key[m.group]
            if m.kind == "up":
                cur = state.get(g.key)
                if cur is None:
                    state[g.key] = {"since": t, "last": t, "channels": {channel}, "url": url}
                else:
                    cur["last"] = t
                    cur["channels"].add(channel)
            elif m.kind == "down":
                state.pop(g.key, None)
            else:
                # "it's a relocation" - also after a takeoff was reported:
                # the timer stops, it wasn't a strike sortie
                state.pop(g.key, None)
                relocs[g.key] = {"at": t, "channel": channel, "url": url}
    out = []
    for key, s in state.items():
        g = by_key[key]
        if now - s["since"] > timedelta(minutes=g.timeout_min):
            continue
        out.append({
            "group": key, "label": g.label,
            "since": s["since"].isoformat(), "last": s["last"].isoformat(),
            "minutes": round((now - s["since"]).total_seconds() / 60.0, 1),
            "launch_window_min": list(g.launch_min),
            "channels": sorted(s["channels"]), "url": s["url"],
        })
    out.sort(key=lambda a: a["since"])
    return out
