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
        self.extra: dict[str, list[str]] = {}
        self.no_spans: set[str] = set()

    def sibling_traces(self, session_id):
        return [] if session_id in self.empty else [f"{session_id}-t", *self.extra.get(session_id, [])]

    def trace_info(self, trace_id):
        return SimpleNamespace(finalization="pending" if trace_id in self.pending else "finalized")

    def spans(self, trace_id):
        if trace_id in self.no_spans:
            return []
        if self.dirty and trace_id == "x2-t":
            return [tspan("notes_write", "127.0.0.1:8102")]
        return [tspan("fetch", "127.0.0.1:8101")]


def make(tmp_path, dirty=False, empty=(), rules=RULES):
    clock = {"t": 100.0}
    tmp_path.mkdir(parents=True, exist_ok=True)
    st = WatchdogState(str(tmp_path / "w.duckdb"))
    c = Confirmer(FakeControl(), FakeSource(dirty, empty), st, lambda role: rules, ingest_wait=10,
                  clock=lambda: clock["t"])
    return c, st, clock


def settle(c, case):
    first = c.check(case)
    return first if first is not None else c.check(case)


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
    assert settle(c, case)["rerun_clean"] is True


def test_check_waits_for_rerun_traces_to_finalize(tmp_path):
    c, st, clock = make(tmp_path)
    case = SimpleNamespace(extra={"confirm_job": "j1"})
    c.control.status = {"status": "done", "rerun_clean": True, "control_clean": True, "rerun_ids": ["x1", "x2"]}
    c.source.pending = {"x2-t"}
    assert c.check(case) is None
    clock["t"] += 11
    assert c.check(case) is None
    c.source.pending = set()
    assert settle(c, case)["rerun_clean"] is True


def test_check_needs_the_same_sibling_set_twice(tmp_path):
    c, st, clock = make(tmp_path)
    case = SimpleNamespace(extra={"confirm_job": "j1"})
    c.control.status = {"status": "done", "rerun_clean": True, "control_clean": True, "rerun_ids": ["x1"]}
    c.check(case)
    clock["t"] += 11
    assert c.check(case) is None
    c.source.extra["x1"] = ["x1-late"]
    assert c.check(case) is None
    assert c.check(case)["rerun_clean"] is True


def test_check_reports_a_failed_job_as_terminal(tmp_path):
    c, st, _ = make(tmp_path)
    c.control.status = {"status": "error", "error": "Traceback with details"}
    assert c.check(SimpleNamespace(extra={"confirm_job": "j1"})) == {"status": "error"}


def test_check_gives_up_after_the_deadline(tmp_path):
    c, st, clock = make(tmp_path)
    case = SimpleNamespace(case_id="c1", run_id="r1", agent="research_agent_1", extra={"confirm_job": "j1"})
    c.start(case)
    assert c.check(case) is None
    clock["t"] += c.deadline + 1
    assert c.check(case) == {"status": "timeout"}


def test_watchdog_not_clean_without_rules_or_final_spans(tmp_path):
    no_rules, _, _ = make(tmp_path / "a", rules=[])
    assert no_rules.watchdog_clean(["x1"], "research_agent") is False
    pending, _, _ = make(tmp_path / "b")
    pending.source.pending = {"x1-t"}
    assert pending.watchdog_clean(["x1"], "research_agent") is False
    empty, _, _ = make(tmp_path / "c")
    empty.source.no_spans = {"x1-t"}
    assert empty.watchdog_clean(["x1"], "research_agent") is False


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
