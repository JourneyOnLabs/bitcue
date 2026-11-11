"""Start BitCue's screen service:  python3 -m z1screen [--config FILE] [--state DIR]"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from . import config as config_mod
from .hub import Hub
from .jobs import JobStore
from .web import Web

HERE = os.path.dirname(os.path.abspath(__file__))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="z1screen")
    ap.add_argument("--config", default="", help="extra settings file (JSON)")
    ap.add_argument("--state", default=os.environ.get("STATE_DIRECTORY", "/var/lib/z1screen"),
                    help="folder for job profiles")
    ap.add_argument("--library", action="append", default=[], help="folder of shipped job profiles")
    args = ap.parse_args(argv)

    cfg = config_mod.load(args.config)
    logging.basicConfig(level=getattr(logging, str(cfg.get("log_level", "INFO")).upper(), logging.INFO),
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    libs = args.library or [os.path.join(os.path.dirname(HERE), "library"), os.path.join(HERE, "library")]
    store = JobStore(args.state, libs)
    hub = Hub(cfg, store)
    web = Web(hub, int(cfg.get("web_port", 8080)))
    logging.getLogger("z1screen").info("starting; %d job profiles known", store.count())
    try:
        asyncio.run(hub.run(web))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
