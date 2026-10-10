from __future__ import annotations

import subprocess
import sys

from common.config import REPO_ROOT


class Launcher:
    def __init__(self, timeout: int = 900):
        self.timeout = timeout

    def run(self, run_id: str, agents: list[str]) -> int:
        cmd = [sys.executable, "-m", "fleet.cli", "run", "--run-id", run_id, "--agent-names", ",".join(agents)]
        try:
            return subprocess.run(cmd, cwd=REPO_ROOT, timeout=self.timeout).returncode
        except subprocess.TimeoutExpired:
            return -1
