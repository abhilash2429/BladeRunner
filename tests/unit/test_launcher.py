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


def test_kill_all_terminates_live_process_trees(tmp_path):
    import threading

    launcher = Launcher(timeout=60, log_dir=tmp_path, command=lambda run_id, agents: [sys.executable, "-c", TREE])
    result = []
    t = threading.Thread(target=lambda: result.append(launcher.run("r3", ["a_1"])))
    t.start()
    log = tmp_path / "r3.log"
    for _ in range(100):
        if log.exists() and log.read_text().strip():
            break
        time.sleep(0.1)
    grandchild = int(log.read_text().split()[0])
    assert pid_alive(grandchild)
    launcher.kill_all()
    t.join(10)
    assert not t.is_alive() and result[0] != 0
    time.sleep(0.5)
    assert not pid_alive(grandchild)


def test_run_after_kill_all_refuses_to_spawn(tmp_path):
    spawned = []

    def command(run_id, agents):
        spawned.append(run_id)
        return [sys.executable, "-c", "pass"]

    launcher = Launcher(timeout=10, log_dir=tmp_path, command=command)
    launcher.kill_all()
    assert launcher.run("r4", ["a_1"]) == -1
    assert spawned == [] and not (tmp_path / "r4.log").exists()


def test_kill_all_is_reentrant_with_the_live_lock(tmp_path):
    import threading

    launcher = Launcher(timeout=10, log_dir=tmp_path)
    done = threading.Event()

    def reenter():
        with launcher._live_lock:
            launcher.kill_all()
        done.set()

    threading.Thread(target=reenter, daemon=True).start()
    assert done.wait(5)


def test_kill_tree_is_bounded_when_process_will_not_die(caplog):
    import subprocess
    from types import SimpleNamespace

    from arena.launcher import _kill_tree

    def never_exits(timeout=None):
        raise subprocess.TimeoutExpired("x", timeout)

    started = time.monotonic()
    _kill_tree(SimpleNamespace(pid=999999, wait=never_exits))
    assert time.monotonic() - started < 30
    assert any("did not exit" in r.getMessage() for r in caplog.records)
