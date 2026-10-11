from __future__ import annotations

import os
import shutil
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

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


def _git_bin_dir() -> str | None:
    git = shutil.which("git")
    if not git:
        return None
    exe_dir = Path(git).resolve().parent
    for root in (exe_dir.parent, exe_dir.parent.parent):
        candidate = root / "bin"
        if (candidate / "sh.exe").exists() or (candidate / "sh").exists():
            return str(candidate)
    return None


def _entire_plugin_bin() -> Path:
    if os.name == "nt":
        local = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        return local / "entire" / "plugins" / "bin"
    if os.environ.get("ENTIRE_PLUGIN_DIR"):
        return Path(os.environ["ENTIRE_PLUGIN_DIR"]) / "bin"
    data = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return data / "entire" / "plugins" / "bin"


def _project_env_names() -> set[str]:
    path = REPO_ROOT / ".env"
    return set(dotenv_values(path)) if path.is_file() else set()


def tool_env(keep: tuple[str, ...] = ()) -> dict[str, str]:
    """Environment for external tools: project secrets from .env are dropped unless named in keep."""
    extra = [str(Path.home() / ".local" / "bin"), str(_entire_plugin_bin())]
    git_bin = _git_bin_dir()
    if git_bin:
        extra.append(git_bin)
    drop = _project_env_names() - set(keep)
    e = {k: v for k, v in os.environ.items() if k not in drop}
    e["PATH"] = os.pathsep.join(extra + [e.get("PATH", "")])
    return e
