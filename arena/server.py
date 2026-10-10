from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal

import uvicorn

from arena.control import create_app as control_app
from arena.launcher import Launcher
from arena.mocks import data_api, docs, notes, unlisted
from arena.state import ArenaState
from common.config import PORTS, RUNS_DIR, load_env


class _Server(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self):
        yield


async def serve(state: ArenaState) -> None:
    apps = {
        "control": control_app(state, Launcher()),
        "data": data_api.create_app(state),
        "notes": notes.create_app(state),
        "docs": docs.create_app(state),
        "unlisted": unlisted.create_app(state),
    }
    servers = [_Server(uvicorn.Config(app, host="127.0.0.1", port=PORTS[name], log_level="warning"))
               for name, app in apps.items()]

    def stop(*_):
        for s in servers:
            s.should_exit = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    await asyncio.gather(*(s.serve() for s in servers))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(RUNS_DIR / "truth.duckdb"))
    args = parser.parse_args()
    load_env()
    RUNS_DIR.mkdir(exist_ok=True)
    asyncio.run(serve(ArenaState(args.db)))


if __name__ == "__main__":
    main()
