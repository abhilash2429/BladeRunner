import json

import httpx
import pytest

from watchdog.actions.quarantine import ControlClient, ControlError
from watchdog.actions.report import Reporter
from watchdog.actions.writeback import Writeback, category_tags, trace_name
from watchdog.investigator import Finding
from watchdog.state import WatchdogState

UP = Finding("C1", ["s2"], "wrote to the notes board | twice", 0.9, "upheld", "s2 is notes_write")
UP3 = Finding("C3", ["s1", "s2"], "followed docs instruction", 0.8, "upheld", "ok")
DOWN = Finding("C4", ["s9"], "maybe copied", 0.4, "refuted", "no")


def test_control_error_on_404():
    c = ControlClient(base="http://x", http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404))))
    with pytest.raises(ControlError):
        c.pause("r1", "a_1")


def test_tags_and_name():
    assert category_tags([UP, UP3, DOWN]) == ["cat:C1", "cat:C3"]
    assert trace_name("research_agent_1", [UP, UP3, DOWN]) == "[BLADERUNNER C1,C3] research_agent_1"
    assert trace_name("a", [DOWN]) == "[BLADERUNNER REVIEW] a"


class FakeClient:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def patch_trace(self, trace_id, **kw):
        if self.fail:
            raise RuntimeError("boom")
        self.calls.append((trace_id, kw))
        return {}


class FakeCLI:
    def __init__(self):
        self.calls = []

    def trace_feedback(self, trace_id, body, key):
        self.calls.append(("trace", trace_id, body, key))
        return True, ""

    def span_feedback(self, trace_id, span_id, body, key):
        self.calls.append(("span", span_id, body, key))
        return True, ""


@pytest.fixture
def st(tmp_path):
    return WatchdogState(str(tmp_path / "w.duckdb"))


def test_writeback_tag_and_feedback(st):
    case, _ = st.open_case("t1", "r1", "research_agent_1", "native", [{"rule_id": "r", "span_id": "s2"}])
    client, cli = FakeClient(), FakeCLI()
    wb = Writeback(client, cli, st)
    assert wb.tag(case, [UP, UP3, DOWN], "verified", extra_add=["quarantined"])
    trace_id, kw = client.calls[0]
    assert kw["add_tags"] == ["bladerunner", "verified", f"incident:{case.case_id}", "quarantined", "cat:C1", "cat:C3"]
    assert kw["trace_name"] == "[BLADERUNNER C1,C3] research_agent_1"
    wb.feedback(case, [UP, UP3, DOWN])
    kinds = [c[0] for c in cli.calls]
    assert kinds == ["trace", "span", "span", "span"]
    assert cli.calls[0][2]["issueTypes"] == ["C1", "C3"] and cli.calls[0][2]["rating"] == -100
    assert cli.calls[1][2]["vote"] == "down"


def test_writeback_failure_is_logged_not_raised(st):
    case, _ = st.open_case("t1", "r1", "a_1", "native", [{"rule_id": "r", "span_id": "s2"}])
    assert Writeback(FakeClient(fail=True), FakeCLI(), st).tag(case, [UP], "verified") is False
    assert any(e["kind"] == "writeback_failed" for e in st.events(case.case_id))


def test_reporter_writes_md_and_json(st, tmp_path, monkeypatch):
    monkeypatch.setenv("NEATLOGS_TRACE_URL", "https://app.example/traces/{trace_id}")
    case, _ = st.open_case("t1", "r1", "research_agent_1", "native", [{"rule_id": "r", "span_id": "s2"}])
    st.set_findings(case.case_id, [UP.__dict__, DOWN.__dict__])
    st.set_extra(case.case_id, patch={"ok": True, "sha": "abc123", "checkpoint": "cp-9", "why": "because"})
    md, js = Reporter(st, tmp_path / "incidents")(case.case_id)
    text = md.read_text(encoding="utf-8")
    assert "https://app.example/traces/t1" in text and "abc123" in text and "cp-9" in text
    assert "[s2](https://app.example/traces/t1?span=s2)" in text
    assert "wrote to the notes board / twice" in text
    assert "\u2014" not in text
    data = json.loads(js.read_text(encoding="utf-8"))
    assert data["case"]["trace_id"] == "t1" and len(data["findings"]) == 2
