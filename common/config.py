from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = REPO_ROOT / "runs"

PORTS: dict[str, int] = {
    "control": 8100,
    "data": 8101,
    "notes": 8102,
    "docs": 8103,
    "unlisted": 8104,
}


def load_env() -> None:
    load_dotenv(REPO_ROOT / ".env", override=False)


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise KeyError(f"missing env var {name}")
    return value


def host(service: str) -> str:
    return f"127.0.0.1:{PORTS[service]}"


def tool_env() -> dict[str, str]:
    local = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    extra = [str(Path.home() / ".local" / "bin"), str(local / "entire" / "plugins" / "bin")]
    e = dict(os.environ)
    e["PATH"] = os.pathsep.join(extra + [e.get("PATH", "")])
    return e
