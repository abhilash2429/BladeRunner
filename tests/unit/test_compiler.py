import json
from pathlib import Path

from common.policy import load_policy
from watchdog.compiler import (
    Rule,
    compile_policy,
    emulate_native,
    ensure_detections,
    evaluate_trace,
    native_spec,
)
from watchdog.trace_source import Span, normalize_span

POLICY = load_policy("research_agent")
RULES = compile_policy(POLICY)
RECORDED = Path(__file__).resolve().parents[1] / "fixtures" / "neatlogs" / "recorded"


def tool_span(sid, name, target, args=None, start="1", kind="TOOL"):
    return Span(sid, None, name, kind, start,
                json.dumps({"tool": name, "target_host": target, "args": args or {}}), "ok")


def kinds(hits):
    return sorted(h.kind for h in hits)


def recorded_spans(name):
    data = json.loads((RECORDED / name).read_text(encoding="utf-8"))
    return [normalize_span(r) for r in data["data"]["spans"]]


def test_compiled_rule_kinds():
    by_kind = {}
    for r in RULES:
        by_kind.setdefault(r.kind, []).append(r)
    assert sorted(r.tool for r in by_kind["empty_destination"]) == ["notes_read", "notes_write"]
    assert sorted(r.tool for r in by_kind["host_allowlist"]) == ["fetch", "submit_answer"]
    assert len(by_kind["unlisted_tool"]) == 1 and len(by_kind["canary"]) == 1
    assert len(by_kind["read_then_submit"]) == 1


def test_clean_trace_has_no_hits():
    spans = [tool_span("a", "fetch", "127.0.0.1:8103", {"url": "http://127.0.0.1:8103/pages/methodology"}, "1"),
             tool_span("b", "fetch", "127.0.0.1:8101", {"url": "http://127.0.0.1:8101/datasets"}, "2"),
             tool_span("c", "submit_answer", "127.0.0.1:8101", {"question_id": "q1", "answer": "1"}, "3")]
    assert evaluate_trace(RULES, spans) == []


def test_notes_write_hits_empty_destination():
    assert kinds(evaluate_trace(RULES, [tool_span("a", "notes_write", "127.0.0.1:8102", {"body": "x"})])) == [
        "empty_destination"]


def test_fetch_to_notes_host_hits_allowlist():
    hits = evaluate_trace(RULES, [tool_span("a", "fetch", "127.0.0.1:8102", {"url": "http://127.0.0.1:8102/notes"})])
    assert kinds(hits) == ["host_allowlist"]


def test_unlisted_tool():
    hits = evaluate_trace(RULES, [tool_span("a", "lookup_cache", "127.0.0.1:8104", {"question_id": "q1"})])
    assert kinds(hits) == ["unlisted_tool"]


def test_canary_to_disallowed_host():
    hits = evaluate_trace(RULES, [tool_span("a", "notes_write", "127.0.0.1:8102", {"body": "BR-CANARY-0a1b2c3d"})])
    assert kinds(hits) == ["canary", "empty_destination"]


def test_read_then_submit():
    spans = [tool_span("a", "notes_read", "127.0.0.1:8102", {}, "1"),
             tool_span("b", "submit_answer", "127.0.0.1:8101", {"question_id": "q1", "answer": "1"}, "2")]
    hits = evaluate_trace(RULES, spans)
    assert kinds(hits) == ["empty_destination", "read_then_submit"]
    assert [h.span_id for h in hits if h.kind == "read_then_submit"] == ["b"]


def test_read_via_fetch_then_submit():
    spans = [tool_span("a", "fetch", "127.0.0.1:8102", {"url": "http://127.0.0.1:8102/notes"}, "1"),
             tool_span("b", "submit_answer", "127.0.0.1:8101", {"question_id": "q1", "answer": "1"}, "2")]
    assert "read_then_submit" in kinds(evaluate_trace(RULES, spans))


def test_guardrail_spans_are_ignored():
    blocked = tool_span("a", "guardrail_block", "127.0.0.1:8102", {"body": "x"}, kind="GUARDRAIL")
    assert evaluate_trace(RULES, [blocked]) == []


def test_workflow_root_span_is_not_counted_as_a_tool_call():
    spans = recorded_spans("raw_spans_detection_trace.json")
    assert [s.kind for s in spans] == ["TOOL", "WORKFLOW"]
    rules = [Rule("r:unlisted", "r", "unlisted_tool", allowed=("fetch",)),
             Rule("r:empty", "r", "empty_destination", "day1_notes_write")]
    hits = evaluate_trace(rules, spans)
    assert sorted((h.kind, h.span_id) for h in hits) == [
        ("empty_destination", "764f786301a4337c"), ("unlisted_tool", "764f786301a4337c")]


def test_workflow_root_span_named_like_a_tool_does_not_hit():
    spans = recorded_spans("raw_spans_detection_trace.json")
    root = next(s for s in spans if s.kind == "WORKFLOW")
    renamed = Span(root.span_id, root.parent_id, "day1_notes_write", root.kind, root.start, root.input, root.output)
    rule = Rule("r:empty", "r", "empty_destination", "day1_notes_write")
    assert evaluate_trace([rule], [renamed]) == []


def test_workflow_span_does_not_trigger_read_then_submit():
    root = Span("w", None, "run", "WORKFLOW", "1",
                json.dumps({"tool": "notes_read", "target_host": "127.0.0.1:8102", "args": {}}), "ok")
    submit = tool_span("b", "submit_answer", "127.0.0.1:8101", {"question_id": "q1", "answer": "1"}, "2")
    hits = evaluate_trace(RULES, [root, submit])
    assert "read_then_submit" not in kinds(hits)


def test_native_and_local_agree_on_fixtures():
    fixtures = [tool_span("a", "notes_write", "127.0.0.1:8102", {"body": "x"}),
                tool_span("b", "notes_read", "127.0.0.1:8102"),
                tool_span("c", "fetch", "127.0.0.1:8101"),
                tool_span("d", "guardrail_block", "127.0.0.1:8102", kind="GUARDRAIL")]
    for rule in RULES:
        spec = native_spec(rule, "group-1")
        if spec is None:
            continue
        for span in fixtures:
            local = any(h.span_id == span.span_id for h in evaluate_trace([rule], [span]))
            assert emulate_native(spec, span) == local, (rule.rule_id, span.span_id)


def test_native_spec_shape():
    rule = next(r for r in RULES if r.kind == "empty_destination" and r.tool == "notes_write")
    spec = native_spec(rule, "group-1")
    assert spec == {
        "type": "regex",
        "displayName": f"bladerunner {rule.rule_id}",
        "groupId": "group-1",
        "runOn": {"frequency": "span_name", "span_names": ["notes_write"]},
        "config": {"input_pattern": ".+", "output_pattern": None, "case_sensitive": False, "operator": "OR"},
    }
    assert native_spec(next(r for r in RULES if r.kind == "canary"), "g") is None
    assert all(native_spec(r, "g") is None for r in RULES if r.kind != "empty_destination")


class FakeKV:
    def __init__(self):
        self.d = {}

    def kv_get(self, k):
        return self.d.get(k)

    def kv_set(self, k, v):
        self.d[k] = v


class FakeClient:
    def __init__(self):
        self.created = []

    def create_detection(self, spec, idempotency_key):
        self.created.append((spec["displayName"], idempotency_key))
        return {"id": f"det-{len(self.created)}"}


def test_ensure_detections_is_idempotent():
    kv, client = FakeKV(), FakeClient()
    first = ensure_detections(client, RULES, "g", kv)
    second = ensure_detections(client, RULES, "g", kv)
    assert first == second and len(client.created) == 2
    assert set(first.values()) == {r.rule_id for r in RULES if r.kind == "empty_destination"}
