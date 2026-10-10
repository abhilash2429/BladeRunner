import json
from types import SimpleNamespace

from common.policy import load_policy
from watchdog.actions.confirm import Confirmer
from watchdog.compiler import compile_policy
from watchdog.state import WatchdogState
from watchdog.trace_source import Span

RULES = compile_policy(load_policy("research_agent"))


def tspan(name, target):
    return Span("s", None, name, "TOOL", "1", json.dumps({"tool": name, "target_host": target, "args": {}}), "")


class FakeControl:
    def __init__(self):
        self.status = {"status": "running"}

    def confirm(self, run_id, agent):
        return {"job_id": "j1", "rerun_ids": ["x1", "x2"]}

    def confirm_status(self, job_id):
        return self.status


class FakeSource:
    def __init__(self, dirty=False, empty=()):
        self.dirty = dirty
        self.empty = set(empty)
        self.pending: set[str] = set()

    def sibling_traces(self, session_id):
        return [] if session_id in self.empty else [f"{session_id}-t"]

    def trace_info(self, trace_id):
        return SimpleNamespace(finalization="pending" if trace_id in self.pending else "finalized")

    def spans(self, trace_id):
        if self.dirty and trace_id == "x2-t":
            return [tspan("notes_write", "127.0.0.1:8102")]
        return [tspan("fetch", "127.0.0.1:8101")]


def make(tmp_path, dirty=False, empty=()):
    clock = {"t": 100.0}
    st = WatchdogState(str(tmp_path / "w.duckdb"))
    c = Confirmer(FakeControl(), FakeSource(dirty, empty), st, lambda role: RULES, ingest_wait=10, clock=lambda: clock["t"])
    return c, st, clock


def test_start_marks_confirm_sessions(tmp_path):
    c, st, _ = make(tmp_path)
    out = c.start(SimpleNamespace(case_id="c1", run_id="r1", agent="research_agent_1"))
    assert out["job_id"] == "j1" and st.kv_get("confirm_session:x1") == "c1"


def test_check_waits_for_done_and_ingest(tmp_path):
    c, st, clock = make(tmp_path)
    case = SimpleNamespace(extra={"confirm_job": "j1"})
    assert c.check(case) is None
    c.control.status = {"status": "done", "rerun_clean": True, "control_clean": True, "rerun_ids": ["x1"]}
    assert c.check(case) is None
    clock["t"] += 11
    assert c.check(case)["rerun_clean"] is True


def test_check_waits_for_rerun_traces_to_finalize(tmp_path):
    c, st, clock = make(tmp_path)
    case = SimpleNamespace(extra={"confirm_job": "j1"})
    c.control.status = {"status": "done", "rerun_clean": True, "control_clean": True, "rerun_ids": ["x1", "x2"]}
    c.source.pending = {"x2-t"}
    assert c.check(case) is None
    clock["t"] += 11
    assert c.check(case) is None
    c.source.pending = set()
    assert c.check(case)["rerun_clean"] is True


def test_check_waits_for_missing_rerun_traces_until_ceiling(tmp_path):
    c, st, clock = make(tmp_path, empty=["x2"])
    case = SimpleNamespace(extra={"confirm_job": "j1"})
    c.control.status = {"status": "done", "rerun_clean": True, "control_clean": True, "rerun_ids": ["x1", "x2"]}
    assert c.check(case) is None
    clock["t"] += 11
    assert c.check(case) is None
    clock["t"] += c.finalize_wait
    assert c.check(case) is not None
    assert c.watchdog_clean(["x1", "x2"], "research_agent") is False


def test_watchdog_clean(tmp_path):
    clean, _, _ = make(tmp_path)
    assert clean.watchdog_clean(["x1", "x2"], "research_agent") is True


def test_watchdog_dirty(tmp_path):
    dirty, _, _ = make(tmp_path, dirty=True)
    assert dirty.watchdog_clean(["x1", "x2"], "research_agent") is False


def test_watchdog_not_clean_when_a_rerun_has_no_traces(tmp_path):
    lagging, _, _ = make(tmp_path, empty=["x2"])
    assert lagging.watchdog_clean(["x1", "x2"], "research_agent") is False
    assert lagging.watchdog_clean(["x1"], "research_agent") is True
