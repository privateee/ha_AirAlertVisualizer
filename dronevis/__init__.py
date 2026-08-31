"""DroneVisualizer - read Telegram air-alert channels, parse them, put the
threats on a map with a live message stream.

Package layout:
    ingest/   fetch raw posts from Telegram (t.me/s/ web preview for now)
    parse/    turn one post into zero or more structured Event records
    geo/      Ukrainian toponym gazetteer + resolution
    dedupe    cluster Events that describe the same object across channels
    areas     areas of interest (Kyiv, ...)
    service   ingest -> parse -> dedupe orchestration
    api       FastAPI app + static web UI
"""

__version__ = "0.9.5"
