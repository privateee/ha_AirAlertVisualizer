"""Common types for ingestion backends."""

from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class RawPost:
    channel: str
    msg_id: int
    url: str
    posted_at: datetime      # timezone-aware UTC
    text: str

    @property
    def posted_at_iso(self) -> str:
        return self.posted_at.isoformat()


class Source(abc.ABC):
    """A source knows how to pull posts for a channel.

    ``fetch_new`` returns posts with ``msg_id > after_id`` (newest run) and
    ``fetch_backfill`` walks history backwards for the first run.
    """

    @abc.abstractmethod
    async def fetch_new(self, channel: str, after_id: int) -> list[RawPost]: ...

    @abc.abstractmethod
    async def fetch_backfill(self, channel: str, pages: int) -> list[RawPost]: ...

    async def aclose(self) -> None:  # pragma: no cover - optional
        return None
