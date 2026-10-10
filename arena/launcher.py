from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

from common.config import REPO_ROOT, RUNS_DIR

log = logging.getLogger(__name__)
KILL_WAIT_SECONDS = 10


def _fleet_command(run_id: str, agents: list[str]) -> list[str]:
    return [sys.executable, "-m", "fleet.cli", "run", "--run-id", run_id, "--agent-names", ",".join(agents)]


def _kill_tree(proc: subprocess.Popen) -> None:
    if sys.platform == "win32":
        try:
            result = subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True,
                                    timeout=KILL_WAIT_SECONDS)
            if result.returncode != 0:
                log.warning("taskkill for pid %s exited %s", proc.pid, result.returncode)
        except subprocess.TimeoutExpired:
            log.warning("taskkill for pid %s timed out", proc.pid)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=KILL_WAIT_SECONDS)
    except subprocess.TimeoutExpired:
        log.warning("pid %s did not exit after kill", proc.pid)


class _KillOnCloseJob:
    def __init__(self, kernel32, handle):
        self._kernel32 = kernel32
        self._handle = handle

    def assign(self, proc: subprocess.Popen) -> None:
        handle = getattr(proc, "_handle", None)
        if self._handle is None or handle is None:
            return
        if not self._kernel32.AssignProcessToJobObject(self._handle, int(handle)):
            log.warning("could not assign pid %s to the kill-on-close job", proc.pid)

    def close(self) -> None:
        if self._handle is not None:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


def _create_kill_on_close_job() -> "_KillOnCloseJob | None":
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
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = ExtendedLimits()
    info.BasicLimitInformation.LimitFlags = 0x2000
    if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
        kernel32.CloseHandle(job)
        return None
    return _KillOnCloseJob(kernel32, job)


class Launcher:
    def __init__(self, timeout: int = 900, log_dir: Path | None = None,
                 command: Callable[[str, list[str]], list[str]] = _fleet_command):
        self.timeout = timeout
        self.log_dir = log_dir if log_dir is not None else RUNS_DIR / "confirm_logs"
        self.command = command
        self._live: set[subprocess.Popen] = set()
        self._live_lock = threading.RLock()
        self._closed = False
        self._job = _create_kill_on_close_job()

    def kill_all(self) -> None:
        with self._live_lock:
            self._closed = True
            live = list(self._live)
        for proc in live:
            _kill_tree(proc)
        self.close()

    def close(self) -> None:
        with self._live_lock:
            if self._job is not None:
                self._job.close()
                self._job = None

    def _spawn(self, run_id: str, agents: list[str]) -> subprocess.Popen | None:
        spawn = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32"
                 else {"start_new_session": True})
        with self._live_lock:
            if self._closed:
                return None
            self.log_dir.mkdir(parents=True, exist_ok=True)
            with open(self.log_dir / f"{run_id}.log", "wb") as out:
                proc = subprocess.Popen(self.command(run_id, agents), cwd=REPO_ROOT, stdout=out,
                                        stderr=subprocess.STDOUT, **spawn)
            self._live.add(proc)
            if self._job is not None:
                self._job.assign(proc)
            return proc

    def run(self, run_id: str, agents: list[str]) -> int:
        proc = self._spawn(run_id, agents)
        if proc is None:
            return -1
        try:
            return proc.wait(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            return -1
        finally:
            with self._live_lock:
                self._live.discard(proc)
