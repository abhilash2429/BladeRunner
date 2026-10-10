import json

import httpx
import pytest

from watchdog.actions.quarantine import ControlClient, ControlError
from watchdog.actions.report import Reporter, span_url, trace_url
from watchdog.actions.writeback import Writeback, category_tags, trace_name
from watchdog.investigator import Finding
from watchdog.neatlogs_client import merge_tags
from watchdog.state import WatchdogState

UP = Finding("C1", ["s2"], "wrote to the notes board | twice", 0.9, "upheld", "s2 is notes_write")
UP3 = Finding("C3", ["s1", "s2"], "followed docs instruction", 0.8, "upheld", "ok")
DOWN = Finding("C4", ["s9"], "maybe copied", 0.4, "refuted", "no")


def test_control_error_on_404():
    c = ControlClient(base="http://x", http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404))))
    with pytest.raises(ControlError):
        c.pause("r1", "a_1")


def test_control_error_on_non_json_success():
    c = ControlClient(base="http://x", http=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, text="<html>not json</html>"))))
    with pytest.raises(ControlError):
        c.confirm("r1", "a_1")


def test_control_client_wraps_invalid_url():
    class Bad:
        def request(self, *a, **k):
            raise httpx.InvalidURL("bad")

    with pytest.raises(ControlError):
        ControlClient(base="http://x", http=Bad()).stop("r1", "a_1")


def test_tags_and_name():
    assert category_tags([UP, UP3, DOWN]) == ["cat:C1", "cat:C3"]
    assert trace_name("research_agent_1", [UP, UP3, DOWN]) == "[BLADERUNNER C1,C3] research_agent_1"
    assert trace_name("a", [DOWN]) == "[BLADERUNNER REVIEW] a"
    seven = [Finding(f"C{i}", ["s"], "x", 0.9, "upheld", "ok") for i in range(1, 8)]
    assert category_tags(seven) == [f"cat:C{i}" for i in range(1, 8)]


class SecretError(RuntimeError):
    def __init__(self):
        super().__init__("response body SECRET-TOKEN")
        self.status = 500


class FakeClient:
    def __init__(self, fail=False, existing=None):
        self.calls = []
        self.fail = fail
        self.existing = existing

    def patch_trace(self, trace_id, **kw):
        if self.fail:
            raise SecretError()
        self.calls.append((trace_id, kw))
        if self.existing is None:
            return {}
        return {"tags": merge_tags(self.existing, kw["add_tags"], kw["remove_tags"])}


class FakeCLI:
    def __init__(self, raise_on=None):
        self.calls = []
        self.raise_on = raise_on

    def trace_feedback(self, trace_id, body, key):
        self.calls.append(("trace", trace_id, body, key))
        if self.raise_on == "trace":
            raise OSError("SECRET-PATH")
        return True, ""

    def span_feedback(self, trace_id, span_id, body, key):
        self.calls.append(("span", span_id, body, key))
        if self.raise_on == span_id:
            raise OSError("SECRET-PATH")
        return True, ""


@pytest.fixture
def st(tmp_path):
    return WatchdogState(str(tmp_path / "w.duckdb"))


def open_case(st, trace_id="t1", agent="a_1"):
    case, _ = st.open_case(trace_id, "r1", agent, "native", [{"rule_id": "r", "span_id": "s2"}])
    return case


def test_writeback_tag_and_feedback(st):
    case = open_case(st, agent="research_agent_1")
    client, cli = FakeClient(), FakeCLI()
    wb = Writeback(client, cli, st)
    assert wb.tag(case, [UP, UP3, DOWN], "verified", extra_add=["quarantined"])
    trace_id, kw = client.calls[0]
    assert kw["add_tags"] == ["bladerunner", "verified", f"incident:{case.case_id}", "quarantined", "cat:C1", "cat:C3"]
    assert kw["trace_name"] == "[BLADERUNNER C1,C3] research_agent_1"
    assert wb.feedback(case, [UP, UP3, DOWN]) is True
    assert [c[0] for c in cli.calls] == ["trace", "span", "span"]
    assert cli.calls[0][2]["issueTypes"] == ["C1", "C3"] and cli.calls[0][2]["rating"] == -100
    spans = {c[1]: c for c in cli.calls if c[0] == "span"}
    assert set(spans) == {"s1", "s2"}
    assert spans["s2"][2]["vote"] == "down"
    assert "C1: wrote to the notes board" in spans["s2"][2]["note"] and "C3: followed docs" in spans["s2"][2]["note"]
    keys = [c[3] for c in cli.calls]
    assert len(set(keys)) == len(keys)


def test_feedback_dedupes_repeated_span_within_finding(st):
    case = open_case(st)
    cli = FakeCLI()
    dup = Finding("C1", ["s2", "s2"], "dup", 0.9, "upheld", "ok")
    assert Writeback(FakeClient(), cli, st).feedback(case, [dup, UP]) is True
    assert [c[1] for c in cli.calls if c[0] == "span"] == ["s2"]


def test_feedback_survives_cli_exceptions(st):
    case = open_case(st)
    cli = FakeCLI(raise_on="trace")
    assert Writeback(FakeClient(), cli, st).feedback(case, [UP, UP3]) is False
    assert [c[0] for c in cli.calls] == ["trace", "span", "span"]
    failed = [e for e in st.events(case.case_id) if e["kind"] == "feedback_failed"]
    assert failed and all(e["data"]["error"] == "OSError" for e in failed)
    assert "SECRET-PATH" not in json.dumps(st.events(case.case_id), default=str)

    case2 = open_case(st, "t2")
    cli2 = FakeCLI(raise_on="s2")
    assert Writeback(FakeClient(), cli2, st).feedback(case2, [UP, UP3]) is False
    assert [c[1] for c in cli2.calls if c[0] == "span"] == ["s2", "s1"]


def test_tag_idempotency_key_depends_on_payload(st):
    case = open_case(st)
    client = FakeClient()
    wb = Writeback(client, FakeCLI(), st)
    wb.tag(case, [UP], "verified")
    wb.tag(case, [UP, UP3], "verified")
    wb.tag(case, [UP], "verified", remove=("quarantined",))
    wb.tag(case, [UP], "verified")
    keys = [kw["idempotency_key"] for _, kw in client.calls]
    assert len(set(keys[:3])) == 3 and keys[0] == keys[3]


def test_tag_keeps_every_add_with_nine_existing_tags(st):
    case = open_case(st)
    existing = [f"user{i}" for i in range(9)]
    client = FakeClient(existing=existing)
    assert Writeback(client, FakeCLI(), st).tag(case, [UP, UP3], "verified", extra_add=["quarantined"]) is True
    merged = merge_tags(existing, client.calls[0][1]["add_tags"])
    assert len(merged) == 10 and all(t in merged for t in client.calls[0][1]["add_tags"])


def test_tag_trims_highest_categories_when_adds_exceed_cap(st):
    case = open_case(st)
    seven = [Finding(f"C{i}", ["s"], "x", 0.9, "upheld", "ok") for i in range(1, 8)]
    client = FakeClient(existing=["agent:a_1"])
    assert Writeback(client, FakeCLI(), st).tag(case, seven, "verified", extra_add=["quarantined"]) is True
    add = client.calls[0][1]["add_tags"]
    assert add == ["bladerunner", "verified", f"incident:{case.case_id}", "quarantined",
                   *[f"cat:C{i}" for i in range(1, 6)]]
    assert client.calls[0][1]["trace_name"] == "[BLADERUNNER C1,C2,C3,C4,C5,C6,C7] a_1"
    assert "agent:a_1" in merge_tags(["agent:a_1"], add)
    tagged = [e for e in st.events(case.case_id) if e["kind"] == "writeback_tags"]
    assert tagged[0]["data"]["trimmed"] == ["cat:C6", "cat:C7"]
    assert not any(e["kind"] == "writeback_failed" for e in st.events(case.case_id))


def test_feedback_keys_depend_on_body(st):
    case = open_case(st)
    cli = FakeCLI()
    wb = Writeback(FakeClient(), cli, st)
    changed = Finding("C1", ["s2"], "different claim", 0.9, "upheld", "ok")
    wb.feedback(case, [UP, UP3])
    wb.feedback(case, [UP, UP3])
    wb.feedback(case, [changed, UP3])
    first, again, other = cli.calls[:3], cli.calls[3:6], cli.calls[6:9]
    assert [c[3] for c in first] == [c[3] for c in again]
    old, new = {c[1]: c[3] for c in first}, {c[1]: c[3] for c in other}
    assert old["t1"] != new["t1"] and old["s2"] != new["s2"] and old["s1"] == new["s1"]


def test_tag_event_has_no_trimmed_key_when_everything_fits(st):
    case = open_case(st)
    assert Writeback(FakeClient(), FakeCLI(), st).tag(case, [UP], "verified")
    tagged = [e for e in st.events(case.case_id) if e["kind"] == "writeback_tags"]
    assert "trimmed" not in tagged[0]["data"]


def test_writeback_failure_is_logged_not_raised(st):
    case = open_case(st)
    assert Writeback(FakeClient(fail=True), FakeCLI(), st).tag(case, [UP], "verified") is False
    events = st.events(case.case_id)
    failed = [e for e in events if e["kind"] == "writeback_failed"]
    assert failed and failed[0]["data"] == {"error": "SecretError", "status": 500}
    assert "SECRET-TOKEN" not in json.dumps(events, default=str)


def test_reporter_writes_md_and_json(st, tmp_path, monkeypatch):
    monkeypatch.setenv("NEATLOGS_TRACE_URL", "https://app.example/traces/{trace_id}")
    case = open_case(st, agent="research_agent_1")
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


def test_reporter_neutralizes_hostile_text(st, tmp_path, monkeypatch):
    monkeypatch.setenv("NEATLOGS_TRACE_URL", "https://app.example/traces/{trace_id}")
    case = open_case(st, agent="agent\n# pwn")
    evil_span = "s]) [evil](http://evil)"
    hostile = Finding("C1", [evil_span],
                      "line1\n| fake | row |\n![x](http://evil/) <script>alert(1)</script> a\u2014b "
                      "http://evil3.example www.evil4.example x\u202ey\u200bz\x07w",
                      0.9, "upheld", "reason\r\n## heading `code` *bold*")
    st.set_findings(case.case_id, [hostile.__dict__])
    why = "a\n```\n# injected\n```\nb"
    st.set_extra(case.case_id, patch={"ok": True, "sha": "abc", "checkpoint": "cp", "why": why,
                                      "paths": ["x](http://evil2)"]})
    md, _ = Reporter(st, tmp_path / "incidents")(case.case_id)
    text = md.read_text(encoding="utf-8")
    rows = [line for line in text.splitlines() if line.startswith("| C1")]
    assert len(rows) == 1
    assert "![x](http://evil/)" not in text and "<script>" not in text
    assert "\\<script\\>" in text
    assert "](http://evil" not in text and "](http://evil2" not in text
    assert "span=s%5D%29%20%5Bevil%5D%28http%3A%2F%2Fevil%29" in text
    assert "\n## heading" not in text and "\n# pwn" not in text
    assert "http://evil3" not in text and "www.evil4" not in text
    assert "http:/&#47;evil3.example" in text and "www&#46;evil4.example" in text
    assert not any(c in text for c in "\u202e\u200b\x07")
    assert "xyzw" in text
    assert "\u2014" not in text
    assert "````\na\n```\n# injected\n```\nb\n````" in text


def test_report_confirm_is_allowlisted(st, tmp_path, monkeypatch):
    monkeypatch.setenv("NEATLOGS_TRACE_URL", "https://app.example/traces/{trace_id}")
    case = open_case(st)
    st.set_findings(case.case_id, [UP.__dict__])
    st.set_extra(case.case_id, confirm={"rerun_clean": True, "control_clean": False, "watchdog_clean": True,
                                        "rerun_ids": ["r9"], "leak": "CONFIRMSECRET"})
    md, js = Reporter(st, tmp_path / "incidents")(case.case_id)
    raw = js.read_text(encoding="utf-8")
    assert "CONFIRMSECRET" not in md.read_text(encoding="utf-8") and "CONFIRMSECRET" not in raw
    assert json.loads(raw)["case"]["extra"]["confirm"] == {"rerun_clean": True, "control_clean": False,
                                                           "watchdog_clean": True}


def test_json_report_keeps_raw_finding_text_capped(st, tmp_path, monkeypatch):
    monkeypatch.setenv("NEATLOGS_TRACE_URL", "https://app.example/traces/{trace_id}")
    case = open_case(st)
    long = Finding("C1", ["s2"], "a|b *x* [y](z) " + "q" * 1500, 0.9, "upheld", "r_1 <b>")
    st.set_findings(case.case_id, [{**long.__dict__, "internal": "NOPE"}])
    md, js = Reporter(st, tmp_path / "incidents")(case.case_id)
    f = json.loads(js.read_text(encoding="utf-8"))["findings"][0]
    assert f["claim"] == ("a|b *x* [y](z) " + "q" * 1500)[:1000] and len(f["claim"]) == 1000
    assert f["reason"] == "r_1 <b>" and f["category"] == "C1" and f["evidence_span_ids"] == ["s2"]
    assert "internal" not in f
    assert "\\*x\\*" in md.read_text(encoding="utf-8")


def test_urls_encode_ids_and_do_not_format_template(monkeypatch):
    monkeypatch.setenv("NEATLOGS_TRACE_URL", "https://app.example/{x}/traces/{trace_id}")
    assert trace_url("a/b c") == "https://app.example/{x}/traces/a%2Fb%20c"
    assert span_url("t", "s&x=1") == "https://app.example/{x}/traces/t?span=s%26x%3D1"


def test_reporter_does_not_dump_raw_evidence(st, tmp_path, monkeypatch):
    monkeypatch.setenv("NEATLOGS_TRACE_URL", "https://app.example/traces/{trace_id}")
    case, _ = st.open_case("t1", "r1", "a_1", "native",
                           [{"rule_id": "r", "span_id": "s2", "kind": "tool", "payload": "BODYTEXT"}])
    st.set_findings(case.case_id, [UP.__dict__])
    st.log_event(case.case_id, "probe", {"token": "SECRETVALUE", "span_id": "s2", "ok": True})
    md, js = Reporter(st, tmp_path / "incidents")(case.case_id)
    text = md.read_text(encoding="utf-8")
    raw = js.read_text(encoding="utf-8")
    for blob in (text, raw):
        assert "BODYTEXT" not in blob and "SECRETVALUE" not in blob and case.idem_key not in blob
    assert "probe" in text
    data = json.loads(raw)
    assert "idem_key" not in data["case"]
    assert data["case"]["evidence"] == [{"rule_id": "r", "span_id": "s2", "kind": "tool"}]
    probe = [e for e in data["events"] if e["kind"] == "probe"][0]
    assert probe["data"] == {"span_id": "s2", "ok": True}
