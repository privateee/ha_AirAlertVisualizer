"""Tiny logging helper. Windows terminals default to cp1252, so force UTF-8
on the stream to keep Cyrillic readable in logs.

``fmt="json"`` (or ``DRONEVIS_LOG_FORMAT=json``) emits one JSON object per
line instead - handy when the add-on / container logs are shipped to a log
aggregator."""

from __future__ import annotations

import io
import json
import logging
import sys
import time

_CONFIGURED = False

_RESERVED = frozenset(vars(logging.makeLogRecord({})))


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        for k, v in record.__dict__.items():          # structured extra=... fields
            if k not in _RESERVED and not k.startswith("_"):
                out[k] = v
        return json.dumps(out, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO", fmt: str = "text") -> None:
    global _CONFIGURED
    if _CONFIGURED:
        logging.getLogger().setLevel(level)
        return

    stream = sys.stderr
    if hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            stream = io.TextIOWrapper(stream.buffer, encoding="utf-8", errors="replace")

    handler = logging.StreamHandler(stream)
    if fmt == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"
            )
        )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # APScheduler is chatty at INFO.
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
