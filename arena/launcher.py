from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
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


def _kill_on_close_job():
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class IoCounters(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes",
                                                    "WriteBytes", "OtherBytes")]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", IoCounters),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = ExtendedLimits()
    info.BasicLimitInformation.LimitFlags = 0x2000
    if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
        return None
    return lambda proc: kernel32.AssignProcessToJobObject(job, int(proc._handle))


class Launcher:
    def __init__(self, timeout: int = 900, log_dir: Path | None = None,
                 command: Callable[[str, list[str]], list[str]] = _fleet_command):
        self.timeout = timeout
        self.log_dir = log_dir if log_dir is not None else RUNS_DIR / "confirm_logs"
        self.command = command
        self._live: set[subprocess.Popen] = set()
        self._live_lock = threading.Lock()
        self._assign_to_job = _kill_on_close_job()

    def kill_all(self) -> None:
        with self._live_lock:
            live = list(self._live)
        for proc in live:
            _kill_tree(proc)

    def run(self, run_id: str, agents: list[str]) -> int:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        spawn = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32"
                 else {"start_new_session": True})
        with open(self.log_dir / f"{run_id}.log", "wb") as log:
            proc = subprocess.Popen(self.command(run_id, agents), cwd=REPO_ROOT, stdout=log,
                                    stderr=subprocess.STDOUT, **spawn)
            if self._assign_to_job:
                self._assign_to_job(proc)
            with self._live_lock:
                self._live.add(proc)
            try:
                return proc.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                _kill_tree(proc)
                return -1
            finally:
                with self._live_lock:
                    self._live.discard(proc)
