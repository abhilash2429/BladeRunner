import sys
import time

from arena.launcher import Launcher

TREE = (
    "import subprocess, sys, time\n"
    "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
    "print(p.pid, flush=True)\n"
    "time.sleep(60)\n"
)


def pid_alive(pid):
    if sys.platform == "win32":
        import subprocess
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True).stdout
        return str(pid) in out
    import os
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_timeout_kills_whole_process_tree_and_logs_output(tmp_path):
    launcher = Launcher(timeout=2, log_dir=tmp_path, command=lambda run_id, agents: [sys.executable, "-c", TREE])
    assert launcher.run("r1", ["a_1"]) == -1
    grandchild = int((tmp_path / "r1.log").read_text().split()[0])
    time.sleep(0.5)
    assert not pid_alive(grandchild)


def test_returns_child_exit_code(tmp_path):
    launcher = Launcher(timeout=10, log_dir=tmp_path,
                        command=lambda run_id, agents: [sys.executable, "-c", "import sys; sys.exit(3)"])
    assert launcher.run("r2", ["a_1"]) == 3
