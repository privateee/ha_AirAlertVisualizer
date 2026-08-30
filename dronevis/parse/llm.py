"""Optional LLM fallback extractor.

Disabled by default (``parse.llm.enabled: false``) so the base install stays
Raspberry-Pi friendly and fully offline. When enabled it is only invoked for
posts the rule engine could not turn into a located threat.

Providers:
    ollama              POST {endpoint}/api/generate      (local, e.g. llama3.1)
    openai_compatible   POST {endpoint} (chat/completions) (llama.cpp, vLLM, ...)

The model is asked for strict JSON; we then re-resolve place names through the
same gazetteer so coordinates stay consistent with the rest of the app.
"""

from __future__ import annotations

import json
import os

import httpx

from ..config import Config
from ..geo.gazetteer import Gazetteer
from ..log import get_logger
from .normalize import fold
from .pipeline import ParsedEvent  # type: ignore  (runtime import ok)
from .threats import ALL_SLUGS

log = get_logger("parse.llm")

_SYSTEM = (
    "You extract air-threat observations from Ukrainian/Russian Telegram "
    "military-monitoring posts. Return ONLY compact JSON: "
    '{"events":[{"threat":"<one of: %s>","count":<int|null>,'
    '"place":"<settlement in Ukrainian nominative|null>",'
    '"destination":"<settlement|null>","source":"<settlement|null>",'
    '"status":"<moving|circling|launch|impact|clear|unknown>"}]}. '
    "No prose. If nothing airborne is described, return {\"events\":[]}."
) % ", ".join(ALL_SLUGS)


class LLMExtractor:
    def __init__(self, cfg: Config, gaz: Gazetteer):
        self.c = cfg.parse.llm
        self.gaz = gaz
        self._client = httpx.Client(timeout=self.c.timeout)

    def should_run(self, text: str, rule_events: list[ParsedEvent]) -> bool:
        if not self.c.only_on_gap:
            return True
        if not rule_events:
            return True
        return not any(e.lat is not None and e.threat_type != "unknown"
                       for e in rule_events)

    # -- provider calls -------------------------------------------------------
    def _call(self, text: str) -> str:
        if self.c.provider == "ollama":
            r = self._client.post(self.c.endpoint, json={
                "model": self.c.model, "stream": False,
                "system": _SYSTEM, "prompt": text,
                "options": {"temperature": 0},
            })
            r.raise_for_status()
            return r.json().get("response", "")
        if self.c.provider == "openai_compatible":
            headers = {}
            key = os.environ.get(self.c.api_key_env, "")
            if key:
                headers["Authorization"] = f"Bearer {key}"
            r = self._client.post(self.c.endpoint, headers=headers, json={
                "model": self.c.model, "temperature": 0,
                "messages": [
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": text},
                ],
            })
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        raise ValueError(f"unknown llm provider {self.c.provider!r}")

    # -- public -----------------------------------------------------------
    def extract(
        self, text: str, *, area_center: tuple[float, float] | None
    ) -> list[ParsedEvent]:
        raw = self._call(text).strip()
        raw = raw[raw.find("{"): raw.rfind("}") + 1] if "{" in raw else "{}"
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("LLM returned non-JSON: %.120s", raw)
            return []

        out: list[ParsedEvent] = []
        oblast_hint = self.gaz.detect_oblast(fold(text))
        for item in data.get("events", []):
            threat = str(item.get("threat", "unknown"))
            if threat not in ALL_SLUGS:
                threat = "unknown"
            prim = self._resolve(item.get("place"), oblast_hint, area_center)
            dest = self._resolve(item.get("destination"), oblast_hint, area_center)
            src = self._resolve(item.get("source"), oblast_hint, area_center)
            heading = None
            if src and prim:
                from ..geo.util import bearing_deg
                heading = bearing_deg(src[1], prim[1])
            elif prim and dest:
                from ..geo.util import bearing_deg
                heading = bearing_deg(prim[1], dest[1])
            out.append(ParsedEvent(
                threat_type=threat,
                threat_raw=None,
                count=_as_int(item.get("count")),
                status=str(item.get("status", "unknown")),
                place_name=prim[0] if prim else None,
                lat=prim[1][0] if prim else None,
                lon=prim[1][1] if prim else None,
                geo_confidence=0.6 if prim else 0.0,
                src_name=src[0] if src else None,
                src_lat=src[1][0] if src else None,
                src_lon=src[1][1] if src else None,
                dest_name=dest[0] if dest else None,
                dest_lat=dest[1][0] if dest else None,
                dest_lon=dest[1][1] if dest else None,
                heading_deg=heading,
                raw_line=text.strip()[:400],
                parse_method="llm",
                parse_confidence=0.55 if prim else 0.3,
            ))
        return out

    def _resolve(self, name, oblast_hint, area_center):
        if not name:
            return None
        hit = self.gaz.lookup(str(name), oblast_hint=oblast_hint,
                              area_center=area_center)
        if not hit:
            return None
        return hit.place.name, hit.place.coord


def _as_int(v):
    try:
        n = int(v)
        return n if 1 <= n <= 200 else None
    except (TypeError, ValueError):
        return None
