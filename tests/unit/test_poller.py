import json
from datetime import datetime, timezone

from common.policy import load_policy
from watchdog.compiler import compile_policy
from watchdog.poller import Poller
from watchdog.state import WatchdogState
from watchdog.trace_source import Span, TraceInfo

NOW = datetime(2026, 10, 11, 9, 0, tzinfo=timezone.utc)
RULES = {"research_agent": compile_policy(load_policy("research_agent"))}


def span(sid, name, target, start):
    return Span(sid, None, name, "TOOL", start, json.dumps({"tool": name, "target_host": target, "args": {}}), "")


class FakeSource:
    def __init__(self):
        self.traces = {
            "t_bad": (TraceInfo("t_bad", "r1", "research_agent_1", ("agent:research_agent_1",), 2, "finalized", ""),
                      [span("a", "fetch", "127.0.0.1:8103", "1"), span("b", "notes_write", "127.0.0.1:8102", "2")]),
            "t_ok": (TraceInfo("t_ok", "r1", "research_agent_2", ("agent:research_agent_2",), 1, "finalized", ""),
                     [span("c", "fetch", "127.0.0.1:8101", "1")]),
            "t_confirm": (TraceInfo("t_confirm", "rc", "research_agent_1", ("agent:research_agent_1",), 1,
                                    "finalized", ""), [span("d", "notes_write", "127.0.0.1:8102", "1")]),
        }
        self.span_calls = 0
        self.spans_fail_once: set[str] = set()
        self.info_fail_once: set[str] = set()

    def list_traces(self, since, until):
        return [info for info, _ in self.traces.values()]

    def trace_info(self, trace_id):
        if trace_id in self.info_fail_once:
            self.info_fail_once.discard(trace_id)
            raise RuntimeError("429 retries exhausted")
        return self.traces[trace_id][0]

    def spans(self, trace_id):
        self.span_calls += 1
        if trace_id in self.spans_fail_once:
            self.spans_fail_once.discard(trace_id)
            raise RuntimeError("429 retries exhausted")
        return self.traces[trace_id][1]


class FakeClient:
    def __init__(self, pages):
        self.pages = pages

    def list_hits(self, detection_id, cursor=None, limit=100):
        return self.pages.get(cursor, ([], None))


class BrokenClient:
    def list_hits(self, detection_id, cursor=None, limit=100):
        raise RuntimeError("400 invalid detection spec")


def make(tmp_path, client=None):
    st = WatchdogState(str(tmp_path / "w.duckdb"))
    st.kv_set("confirm_session:rc", "case-x")
    p = Poller(FakeSource(), st, client=client, now=lambda: NOW)
    p.rules_by_role = RULES
    return p, st


def test_sweep_flags_only_violating_trace(tmp_path):
    p, st = make(tmp_path)
    flags = p.sweep()
    assert [f.trace_id for f in flags] == ["t_bad"]
    assert flags[0].run_id == "r1" and flags[0].source == "local"
    assert {h["span_id"] for h in flags[0].hits} == {"b"}


def test_sweep_skips_unchanged_traces(tmp_path):
    p, _ = make(tmp_path)
    p.sweep()
    calls = p.source.span_calls
    assert p.sweep() == [] and p.source.span_calls == calls


def test_native_hits_dedupe(tmp_path):
    client = FakeClient({None: ([{"id": "h1", "traceId": "t_bad", "spanId": "b"},
                                 {"id": "h2", "traceId": "t_confirm", "spanId": "d"}], None)})
    p, _ = make(tmp_path, client)
    p.detections = {"det-1": "research_agent:empty_destination:notes_write"}
    flags = p.poll_native()
    assert [f.trace_id for f in flags] == ["t_bad"] and flags[0].source == "native"
    assert p.poll_native() == []


def test_tick_opens_cases(tmp_path):
    client = FakeClient({None: ([{"id": "h1", "traceId": "t_bad", "spanId": "b"}], None)})
    p, st = make(tmp_path, client)
    p.detections = {"det-1": "research_agent:empty_destination:notes_write"}
    changed = p.tick()
    assert len(changed) == 1
    case = st.case_for_trace("t_bad")
    assert case.status == "flagged" and case.source == "both"


def test_sweep_retries_trace_after_source_error(tmp_path):
    p, st = make(tmp_path)
    p.source.spans_fail_once = {"t_bad"}
    first = p.sweep()
    assert first == [] and st.kv_get("seen:t_bad") is None
    assert st.kv_get("last_poll_error").startswith("RuntimeError")
    second = p.sweep()
    assert [f.trace_id for f in second] == ["t_bad"]
    assert p.sweep() == []


def test_native_hit_retried_after_trace_info_error(tmp_path):
    client = FakeClient({None: ([{"id": "h1", "traceId": "t_bad", "spanId": "b"}], None)})
    p, st = make(tmp_path, client)
    p.detections = {"det-1": "research_agent:empty_destination:notes_write"}
    p.source.info_fail_once = {"t_bad"}
    assert p.poll_native() == [] and st.kv_get("hit:h1") is None
    assert [f.trace_id for f in p.poll_native()] == ["t_bad"]
    assert p.poll_native() == []


def test_tick_survives_native_poll_failure(tmp_path):
    p, st = make(tmp_path, BrokenClient())
    p.detections = {"det-1": "research_agent:empty_destination:notes_write"}
    changed = p.tick()
    assert len(changed) == 1 and st.case_for_trace("t_bad").source == "local"
    assert st.kv_get("last_poll_error").startswith("RuntimeError")


def test_sweep_leaves_pending_trace_unseen_until_finalized(tmp_path):
    p, st = make(tmp_path)
    info, spans = p.source.traces["t_bad"]
    p.source.traces["t_bad"] = (TraceInfo(info.trace_id, info.session_id, info.agent, info.tags, 2, "pending", ""),
                                spans)
    assert p.sweep() == [] and st.kv_get("seen:t_bad") is None
    p.source.traces["t_bad"] = (info, spans)
    assert [f.trace_id for f in p.sweep()] == ["t_bad"]


def test_native_pagination_does_not_stop_at_seen_hit(tmp_path):
    client = FakeClient({
        None: ([{"id": "h1", "traceId": "t_bad", "spanId": "b"}], "c1"),
        "c1": ([{"id": "h0", "traceId": "t_ok", "spanId": "c"}], None),
    })
    p, st = make(tmp_path, client)
    p.detections = {"det-1": "research_agent:empty_destination:notes_write"}
    st.kv_set("hit:h1", "1")
    assert [f.trace_id for f in p.poll_native()] == ["t_ok"]


def test_native_pagination_survives_repeated_cursor(tmp_path):
    client = FakeClient({None: ([{"id": "h1", "traceId": "t_bad", "spanId": "b"}], "c1"),
                         "c1": ([{"id": "h1", "traceId": "t_bad", "spanId": "b"}], "c1")})
    p, _ = make(tmp_path, client)
    p.detections = {"det-1": "research_agent:empty_destination:notes_write"}
    assert [f.trace_id for f in p.poll_native()] == ["t_bad"]


def test_native_failure_keeps_flags_from_earlier_pages(tmp_path):
    class Flaky:
        def list_hits(self, detection_id, cursor=None, limit=100):
            if cursor is None:
                return [{"id": "h1", "traceId": "t_bad", "spanId": "b"}], "c1"
            raise RuntimeError("500 boom")

    p, st = make(tmp_path, Flaky())
    p.detections = {"det-1": "research_agent:empty_destination:notes_write"}
    assert [f.trace_id for f in p.poll_native()] == ["t_bad"]
    assert st.kv_get("last_poll_error").startswith("RuntimeError")


def test_sweep_skips_non_agent_traces(tmp_path):
    p, _ = make(tmp_path)
    p.source.traces["t_wd"] = (TraceInfo("t_wd", None, None, (), 1, "finalized", ""),
                               [span("w", "notes_write", "127.0.0.1:8102", "1")])
    assert [f.trace_id for f in p.sweep()] == ["t_bad"]


def test_tick_survives_list_traces_failure(tmp_path):
    p, st = make(tmp_path)

    def boom(since, until):
        raise RuntimeError("409 exhausted")

    p.source.list_traces = boom
    assert p.tick() == []
    assert st.kv_get("last_poll_error").startswith("RuntimeError")

