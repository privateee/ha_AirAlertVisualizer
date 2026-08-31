"""Command line entry point.

    python -m dronevis run          start the web UI + background poller
    python -m dronevis ingest       fetch new posts once, then exit
    python -m dronevis reparse      rebuild all events/clusters from raw posts
                                   (--since-hours N for an incremental window)
    python -m dronevis parse "..."  show how one message is parsed
    python -m dronevis stats        print row counts
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from .config import load_config
from .log import setup_logging


def _add_config_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("--config", "-c", help="path to config.yaml")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dronevis")
    sub = parser.add_subparsers(dest="cmd")

    p_run = sub.add_parser("run", help="start the web server + poller")
    _add_config_arg(p_run)
    p_run.add_argument("--host")
    p_run.add_argument("--port", type=int)
    p_run.add_argument("--reload", action="store_true")

    for name in ("ingest", "stats", "ha"):
        _add_config_arg(sub.add_parser(name))

    p_reparse = sub.add_parser("reparse", help="rebuild events/clusters from raw posts")
    _add_config_arg(p_reparse)
    p_reparse.add_argument(
        "--since-hours", type=float, default=None,
        help="only reparse the last N hours (incremental); default is a full rebuild",
    )

    p_parse = sub.add_parser("parse", help="debug: parse a single message")
    _add_config_arg(p_parse)
    p_parse.add_argument("text")
    p_parse.add_argument("--channel", default=None)

    args = parser.parse_args(argv)
    cmd = args.cmd or "run"
    if getattr(args, "config", None):
        os.environ["DRONEVIS_CONFIG"] = args.config
    cfg = load_config(getattr(args, "config", None))
    setup_logging(cfg.log_level, cfg.log_format)

    if cmd == "run":
        import uvicorn

        host = args.host or cfg.server.host
        port = args.port or cfg.server.port
        uvicorn.run(
            "dronevis.api:app", host=host, port=port, reload=bool(args.reload),
            log_level=cfg.log_level.lower(),
        )
        return 0

    if cmd == "ingest":
        from .service import Service

        async def _go() -> None:
            svc = Service(cfg)
            try:
                stats = await svc.ingest_once()
                print(json.dumps(stats, ensure_ascii=False, indent=2))
            finally:
                await svc.aclose()

        asyncio.run(_go())
        return 0

    if cmd == "reparse":
        from .service import Service

        svc = Service(cfg)
        try:
            sh = getattr(args, "since_hours", None)
            res = svc.reparse_since(sh) if sh and sh > 0 else svc.reparse_all()
            print(json.dumps(res, ensure_ascii=False, indent=2))
        finally:
            asyncio.run(svc.aclose())
        return 0

    if cmd == "stats":
        from .db import Database

        db = Database(cfg.database_path)
        row = db.query_one(
            "SELECT (SELECT COUNT(*) FROM raw_message) rm, "
            "(SELECT COUNT(*) FROM event) ev, (SELECT COUNT(*) FROM cluster) cl"
        )
        print(f"raw_messages={row['rm']}  events={row['ev']}  clusters={row['cl']}")
        print(f"last_ingest={db.get_meta('last_ingest')}")
        db.close()
        return 0

    if cmd == "ha":
        from .db import Database
        from .ha import compute_state

        db = Database(cfg.database_path)
        print(json.dumps(compute_state(db, cfg), ensure_ascii=False, indent=2))
        db.close()
        return 0

    if cmd == "parse":
        from .parse.pipeline import Parser

        parser_ = Parser(cfg)
        center = cfg.default_area.center or (50.4501, 30.5234)
        events = parser_.parse(args.text, channel=args.channel, area_center=center)
        if not events:
            print("(no events)")
        for e in events:
            print(json.dumps(e.to_row(), ensure_ascii=False, indent=2))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
