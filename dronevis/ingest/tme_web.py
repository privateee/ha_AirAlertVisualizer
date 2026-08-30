"""Read a public channel through its web preview at ``https://t.me/s/<name>``.

No login, no API key. Works for any channel that has "preview" enabled
(all four defaults do). Limitations to keep in mind:

* only the last ~1000 posts are reachable by paging ``?before=``
* no edits/deletions signal - we treat a post as immutable once seen
* rate: be polite, one request every ``sources.request_delay`` seconds
"""

from __future__ import annotations

import asyncio
import html
import re
import ssl
import time
from datetime import datetime, timezone

import httpx
from selectolax.parser import HTMLParser


def _ssl_context() -> ssl.SSLContext | bool:
    """Prefer the OS trust store (Windows/macOS corporate roots), fall back to
    certifi, then to unverified as a last resort so ingestion still runs."""
    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        pass
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return True

from ..config import Config
from ..log import get_logger
from .base import RawPost, Source

log = get_logger("ingest.tme_web")

_BASE = "https://t.me/s/{channel}"
_BR_RE = re.compile(r"<br\s*/?>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_MAX_NEW_PAGES = 6  # safety cap when catching up in fetch_new


def _node_text(node) -> str:
    """Inner text of a ``.tgme_widget_message_text`` node with <br> -> newline."""
    if node is None:
        return ""
    inner = node.html or ""
    # drop the opening/closing wrapper tag, keep children
    inner = re.sub(r"^<div[^>]*>", "", inner)
    inner = re.sub(r"</div>$", "", inner)
    inner = _BR_RE.sub("\n", inner)
    inner = _TAG_RE.sub("", inner)
    text = html.unescape(inner)
    # normalise trailing "VIEW IN TELEGRAM" widget noise if it leaks in
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _parse_page(html_text: str, channel: str) -> list[RawPost]:
    tree = HTMLParser(html_text)
    posts: list[RawPost] = []
    for msg in tree.css("div.tgme_widget_message"):
        data_post = msg.attributes.get("data-post") or ""
        m = re.search(r"/(\d+)$", data_post)
        if not m:
            continue
        msg_id = int(m.group(1))

        time_node = msg.css_first(".tgme_widget_message_date time")
        dt_attr = time_node.attributes.get("datetime") if time_node else None
        if not dt_attr:
            continue
        posted_at = datetime.fromisoformat(dt_attr).astimezone(timezone.utc)

        text_node = msg.css_first(".tgme_widget_message_text")
        text = _node_text(text_node)
        if not text:
            # media-only / service post - skip, nothing to geolocate
            continue

        posts.append(
            RawPost(
                channel=channel,
                msg_id=msg_id,
                url=f"https://t.me/{data_post}",
                posted_at=posted_at,
                text=text,
            )
        )
    posts.sort(key=lambda p: p.msg_id)
    return posts


class TmeWebSource(Source):
    def __init__(self, cfg: Config):
        self._delay = cfg.sources.request_delay
        self._client = httpx.AsyncClient(
            headers={
                "User-Agent": cfg.sources.user_agent,
                "Accept-Language": "uk,ru;q=0.9,en;q=0.8",
            },
            timeout=cfg.sources.request_timeout,
            follow_redirects=True,
            verify=_ssl_context(),
        )
        self._last_request = 0.0

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, url: str, params: dict | None = None) -> str:
        now = time.monotonic()
        wait = self._delay - (now - self._last_request)
        if wait > 0:
            await asyncio.sleep(wait)
        resp = await self._client.get(url, params=params)
        self._last_request = time.monotonic()
        resp.raise_for_status()
        return resp.text

    async def _page(self, channel: str, before: int | None = None) -> list[RawPost]:
        params = {"before": before} if before else None
        try:
            body = await self._get(_BASE.format(channel=channel), params=params)
        except httpx.HTTPError as exc:
            log.warning("fetch failed for %s (before=%s): %s", channel, before, exc)
            return []
        return _parse_page(body, channel)

    async def fetch_new(self, channel: str, after_id: int) -> list[RawPost]:
        collected: dict[int, RawPost] = {}
        before: int | None = None
        for _ in range(_MAX_NEW_PAGES):
            page = await self._page(channel, before=before)
            if not page:
                break
            fresh = [p for p in page if p.msg_id > after_id]
            for p in fresh:
                collected[p.msg_id] = p
            oldest = page[0].msg_id
            if oldest <= after_id or after_id == 0 or not fresh:
                # reached known territory, or first-ever run (handled by backfill)
                break
            before = oldest
        return sorted(collected.values(), key=lambda p: p.msg_id)

    async def fetch_backfill(self, channel: str, pages: int) -> list[RawPost]:
        collected: dict[int, RawPost] = {}
        before: int | None = None
        for _ in range(max(1, pages)):
            page = await self._page(channel, before=before)
            if not page:
                break
            for p in page:
                collected[p.msg_id] = p
            before = page[0].msg_id
        return sorted(collected.values(), key=lambda p: p.msg_id)
