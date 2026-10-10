from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Callable

from common.config import REPO_ROOT, RUNS_DIR


def _fleet_command(run_id: str, agents: list[str]) -> list[str]:
    return [sys.executable, "-m", "fleet.cli", "run", "--run-id", run_id, "--agent-names", ",".join(agents)]


def _kill_tree(proc: subprocess.Popen) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    proc.wait()


class Launcher:
    def __init__(self, timeout: int = 900, log_dir: Path | None = None,
                 command: Callable[[str, list[str]], list[str]] = _fleet_command):
        self.timeout = timeout
        self.log_dir = log_dir if log_dir is not None else RUNS_DIR / "confirm_logs"
        self.command = command

    def run(self, run_id: str, agents: list[str]) -> int:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        spawn = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32"
                 else {"start_new_session": True})
        with open(self.log_dir / f"{run_id}.log", "wb") as log:
            proc = subprocess.Popen(self.command(run_id, agents), cwd=REPO_ROOT, stdout=log,
                                    stderr=subprocess.STDOUT, **spawn)
            try:
                return proc.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                _kill_tree(proc)
                return -1
