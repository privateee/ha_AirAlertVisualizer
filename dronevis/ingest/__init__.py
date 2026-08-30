"""Ingestion backends. ``get_source(cfg)`` returns the configured one."""

from __future__ import annotations

from ..config import Config
from .base import RawPost, Source
from .tme_web import TmeWebSource

__all__ = ["RawPost", "Source", "TmeWebSource", "get_source"]


def get_source(cfg: Config) -> Source:
    backend = cfg.sources.backend
    if backend == "tme_web":
        return TmeWebSource(cfg)
    raise ValueError(
        f"Unknown sources.backend={backend!r} (only 'tme_web' is implemented)"
    )
