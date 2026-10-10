import json
from datetime import datetime, timezone
from pathlib import Path

from watchdog.neatlogs_client import NeatlogsError
from watchdog.trace_source import (
    FrozenSource,
    LiveSource,
    Span,
    compact_tree,
    normalize_span,
    text_of,
    tool_call,
    trace_info_from,
)

RECORDED = Path(__file__).resolve().parents[1] / "fixtures" / "neatlogs" / "recorded"
TOOL_INPUT = '{"tool":"day1_notes_write","target_host":"127.0.0.1:8102","args":{"body":"hello"}}'


def recorded(name):
    return json.loads((RECORDED / name).read_text(encoding="utf-8"))["data"]


def test_normalize_recorded_raw_spans():
    workflow, tool = (normalize_span(r) for r in recorded("raw_spans.json")["spans"])
    assert workflow == Span("7c966446b0d46649", None, "day1-run", "WORKFLOW", "2026-10-10T16:19:56.634Z", "{}", "null")
    assert tool == Span("bc89bfb07efed2de", "7c966446b0d46649", "day1_notes_write", "TOOL",
                        "2026-10-10T16:19:56.634Z", TOOL_INPUT, '"ok"')


def test_normalize_recorded_listing_has_no_io():
    tool = normalize_span(recorded("spans.json")["spans"][1])
    assert tool.name == "day1_notes_write" and tool.kind == "TOOL" and tool.parent_id == "7c966446b0d46649"
    assert tool.input == "" and tool.output == ""


def test_tool_call_from_recorded_tool_span():
    tool = normalize_span(recorded("raw_spans.json")["spans"][1])
    assert tool_call(tool) == {"tool": "day1_notes_write", "target_host": "127.0.0.1:8102", "args": {"body": "hello"}}


def test_tool_call_from_detection_trace_workflow_and_tool():
    spans = [normalize_span(r) for r in recorded("raw_spans_detection_trace.json")["spans"]]
    assert {tool_call(s)["target_host"] for s in spans} == {"127.0.0.1:8102"}


def test_tool_call_none_for_workflow_with_empty_input():
    assert tool_call(normalize_span(recorded("raw_spans.json")["spans"][0])) is None


def test_tool_call_finds_nested_kwargs():
    s = Span("s", None, "fetch", "TOOL", "", json.dumps({"kwargs": {"tool": "fetch", "target_host": "h:1",
                                                                     "args": {"url": "u"}}}), "")
    assert tool_call(s)["target_host"] == "h:1"


def test_tool_call_none_for_plain_input():
    assert tool_call(Span("s", None, "chat", "LLM", "", "hello", "")) is None


def test_text_of_handles_neatlogs_io_objects():
    assert text_of({"mimeType": "application/json", "preview": "abc", "truncated": False}) == "abc"
    assert text_of({"available": True, "truncated": False, "value": "full"}) == "full"
    assert text_of({"a": 1}) == '{"a": 1}'
    assert text_of(None) == ""


def test_trace_info_from_recorded_trace():
    info = trace_info_from(recorded("trace_799e_unmodified.json"))
    assert info.trace_id == "799e6fc99b3ee9e28124102b6c4869a4"
    assert info.session_id == "day1-1791649060"
    assert info.agent == "day1_agent" and info.tags == ("agent:day1_agent",)
    assert info.spans_count == 2 and info.finalization == "finalized"
    assert info.started_at == "2026-10-10T16:17:40.515Z"


def test_trace_info_extracts_agent_tag():
    info = trace_info_from({"traceId": "t1", "sessionId": "r1", "tags": ["agent:research_agent_2", "x"],
                            "spansCount": 7, "finalizationStatus": "pending", "startedAt": "2026-10-11T09:00:00Z"})
    assert info.agent == "research_agent_2" and info.session_id == "r1" and info.spans_count == 7


def test_compact_tree_truncates_and_hides_llm_prompts():
    spans = [Span("s1", None, "chat", "LLM", "1", "p" * 2000, "a" * 2000),
             Span("s2", "s1", "fetch", "TOOL", "2", "i" * 2000, "o" * 2000)]
    tree = compact_tree(spans, limit=100)
    assert tree[0]["input"] == "<llm prompt omitted>" and len(tree[0]["output"]) <= 101
    assert len(tree[1]["input"]) <= 101 and tree[1]["parent"] == "s1"


def test_frozen_source(tmp_path):
    (tmp_path / "spans").mkdir()
    manifest = {"traces": {"t1": {"run_id": "r1", "agent": "research_agent_1", "session_id": "r1",
                                  "tags": ["agent:research_agent_1"], "started_at": "x"},
                           "t2": {"run_id": "r1", "agent": "research_agent_2", "session_id": "r1",
                                  "tags": ["agent:research_agent_2"], "started_at": "y"}},
                "policies": {}}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    span = normalize_span(recorded("raw_spans.json")["spans"][1])
    (tmp_path / "spans" / "t1.json").write_text(json.dumps([span.__dict__]))
    src = FrozenSource(tmp_path)
    assert [t.trace_id for t in src.list_traces(None, None)] == ["t1", "t2"]
    assert src.spans("t1") == [span]
    assert src.payload("t1", span.span_id)["input"] == TOOL_INPUT
    assert sorted(src.sibling_traces("r1")) == ["t1", "t2"]
    assert src.trace_info("t2").agent == "research_agent_2"


class FakeClient:
    def __init__(self, raw=None):
        self.raw = raw if raw is not None else recorded("raw_spans.json")["spans"]
        self.payload_calls = []

    def list_traces(self, **kwargs):
        self.kwargs = kwargs
        return [{"traceId": "t1", "sessionId": "r1", "tags": ["agent:a_1"], "spansCount": 2}]

    def get_trace(self, trace_id):
        return recorded("trace_799e_unmodified.json")

    def get_raw_spans(self, trace_id):
        return self.raw

    def get_payload(self, trace_id, span_id, fields=("input", "output")):
        self.payload_calls.append((trace_id, span_id))
        return recorded("payload.json")


def test_live_source_reads_raw_spans_sorted_root_first():
    raw = list(reversed(recorded("raw_spans.json")["spans"]))
    src = LiveSource(FakeClient(raw), now=lambda: datetime(2026, 10, 11, tzinfo=timezone.utc))
    spans = src.spans("t1")
    assert [s.kind for s in spans] == ["WORKFLOW", "TOOL"]
    assert tool_call(spans[1])["target_host"] == "127.0.0.1:8102"


def test_live_source_fetches_payload_when_preview_truncated():
    raw = json.loads(json.dumps(recorded("raw_spans.json")["spans"]))
    raw[1]["input"] = {"mimeType": "application/json", "preview": '{"tool":"day1_notes_wri', "truncated": True}
    client = FakeClient(raw)
    spans = LiveSource(client).spans("t1")
    assert spans[1].input == TOOL_INPUT and spans[1].output == '"ok"'
    assert client.payload_calls == [("t1", "bc89bfb07efed2de")]
    assert spans[0].input == "{}"


def test_live_source_skips_payload_when_not_truncated():
    client = FakeClient()
    LiveSource(client).spans("t1")
    assert client.payload_calls == []


def test_live_source_payload_unwraps_fields():
    p = LiveSource(FakeClient()).payload("t", "s")
    assert p == {"input": TOOL_INPUT, "output": '"ok"'}


def test_live_source_lists_and_siblings():
    client = FakeClient()
    src = LiveSource(client, now=lambda: datetime(2026, 10, 11, tzinfo=timezone.utc))
    since = datetime(2026, 10, 10, tzinfo=timezone.utc)
    assert [t.trace_id for t in src.list_traces(since, None)] == ["t1"]
    assert client.kwargs["workflow_name"] == "arena-agent" and client.kwargs["since"] == since
    assert src.sibling_traces("r1") == ["t1"]
    assert client.kwargs["session_id"] == "r1"
    assert src.trace_info("t1").agent == "day1_agent"


def test_text_of_wrapper_without_value_is_empty():
    assert text_of({"mimeType": "application/json", "preview": None, "truncated": False}) == ""
    assert text_of({"available": False, "truncated": False}) == ""
    assert text_of({"mimeType": None}) == ""


def test_live_source_falls_back_to_preview_when_payload_fails(caplog):
    raw = json.loads(json.dumps(recorded("raw_spans.json")["spans"]))
    raw[1]["input"] = {"mimeType": "application/json", "preview": '{"tool":"day1_notes_wri', "truncated": True}
    raw[1]["output"] = {"mimeType": "application/json", "preview": '"o', "truncated": True}

    class Failing(FakeClient):
        def get_payload(self, trace_id, span_id, fields=("input", "output")):
            raise NeatlogsError(403, "forbidden")

    with caplog.at_level("WARNING"):
        spans = LiveSource(Failing(raw)).spans("t1")
    assert spans[1].input == '{"tool":"day1_notes_wri' and spans[1].output == '"o'
    assert len([r for r in caplog.records if r.levelname == "WARNING"]) == 1


def test_tool_call_decodes_double_encoded_input():
    inner = {"tool": "fetch", "target_host": "h:2", "args": {"url": "u"}}
    s = Span("s", None, "fetch", "TOOL", "", json.dumps(json.dumps(inner)), "")
    assert tool_call(s) == {"tool": "fetch", "target_host": "h:2", "args": {"url": "u"}}


def test_tool_call_none_for_json_string_that_is_not_json():
    assert tool_call(Span("s", None, "chat", "LLM", "", json.dumps("hello"), "")) is None


def test_sort_uses_parsed_time_with_span_id_tiebreak():
    raw = [
        {"spanId": "b", "parentSpanId": "r", "spanName": "n", "spanType": "TOOL", "startedAt": "2026-10-10T16:19:56Z"},
        {"spanId": "a", "parentSpanId": "r", "spanName": "n", "spanType": "TOOL", "startedAt": "2026-10-10T16:19:56.000Z"},
        {"spanId": "c", "parentSpanId": "r", "spanName": "n", "spanType": "TOOL", "startedAt": "2026-10-10T16:19:56.5Z"},
        {"spanId": "r", "parentSpanId": None, "spanName": "n", "spanType": "WORKFLOW", "startedAt": "2026-10-10T16:19:56Z"},
    ]
    spans = LiveSource(FakeClient(raw)).spans("t1")
    assert [s.span_id for s in spans] == ["r", "a", "b", "c"]
