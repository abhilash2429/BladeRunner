import json
from types import SimpleNamespace

import httpx

from arena.tools import ALLOWED_HOSTS, TOOLS, ToolExecutor, openai_tool_defs
from common.config import host
from common.tracing import NullTracer


def make_executor(guard=None, record=None):
    record = record if record is not None else []

    def handler(request: httpx.Request) -> httpx.Response:
        record.append(request)
        return httpx.Response(200, text='{"ok": true}')

    http = httpx.Client(transport=httpx.MockTransport(handler))
    return ToolExecutor("run1", "research_agent_1", http, tracer=NullTracer, guard=guard), record


def test_tool_defs_shape():
    defs = openai_tool_defs(["fetch", "submit_answer"])
    assert [d["function"]["name"] for d in defs] == ["fetch", "submit_answer"]
    assert defs[0]["type"] == "function" and "parameters" in defs[0]["function"]


def test_registry_effects():
    assert TOOLS["notes_write"].effect == "write" and TOOLS["notes_read"].effect == "read"
    assert host("notes") in ALLOWED_HOSTS and host("control") not in ALLOWED_HOSTS


def test_fetch_sends_arena_headers_to_target():
    ex, rec = make_executor()
    out = ex.call("fetch", {"url": f"http://{host('docs')}/pages/methodology"}, step=2)
    assert out == '{"ok": true}'
    req = rec[0]
    assert req.url.host == "127.0.0.1" and req.url.port == 8103 and req.url.path == "/pages/methodology"
    assert req.headers["x-arena-run"] == "run1" and req.headers["x-arena-step"] == "2"
    assert req.headers["x-arena-tool"] == "fetch" and req.headers["x-arena-agent"] == "research_agent_1"


def test_fetch_to_outside_host_is_refused_without_request():
    ex, rec = make_executor()
    out = ex.call("fetch", {"url": "http://example.com/x"}, step=1)
    assert out.startswith("error") and rec == []


def test_fetch_to_control_port_is_refused():
    ex, rec = make_executor()
    out = ex.call("fetch", {"url": f"http://{host('control')}/runs"}, step=1)
    assert out.startswith("error") and rec == []


def test_notes_write_posts_json():
    ex, rec = make_executor()
    ex.call("notes_write", {"question_id": "q1", "answer": "1", "body": "b"}, step=3)
    assert rec[0].method == "POST" and rec[0].url.port == 8102
    assert json.loads(rec[0].content) == {"question_id": "q1", "answer": "1", "body": "b"}


def test_submit_answer_routes_to_data_submit():
    ex, rec = make_executor()
    ex.call("submit_answer", {"question_id": "q1", "answer": "61250"}, step=4)
    assert rec[0].url.port == 8101 and rec[0].url.path == "/submit"


def test_guardrail_block_skips_request():
    guard = SimpleNamespace(check_call=lambda a, t, h, args: "notes are off-policy" if t == "notes_write" else None,
                            filter_result=lambda a, t, text: text)
    ex, rec = make_executor(guard=guard)
    out = ex.call("notes_write", {"body": "x"}, step=1)
    assert out == "blocked by guardrail: notes are off-policy" and rec == []


def test_guardrail_filter_applies_to_results():
    guard = SimpleNamespace(check_call=lambda *a: None, filter_result=lambda a, t, text: text.upper())
    ex, _ = make_executor(guard=guard)
    assert ex.call("notes_read", {}, step=1) == '{"OK": TRUE}'


def test_unknown_tool():
    ex, rec = make_executor()
    assert ex.call("delete_everything", {}, step=1).startswith("error") and rec == []
