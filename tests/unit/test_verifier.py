import json

from watchdog.investigator import Finding
from watchdog.trace_source import Span
from watchdog.verifier import Verifier, decide


class FakeSource:
    def spans(self, trace_id):
        return {"t1": [Span("s1", None, "fetch", "TOOL", "1", "{}", "docs"),
                       Span("s2", None, "notes_write", "TOOL", "2", "{}", "ok")],
                "t2": [Span("s9", None, "notes_read", "TOOL", "1", "{}", "notes")]}[trace_id]

    def payload(self, trace_id, span_id):
        return {"input": f"in-{span_id}", "output": f"out-{span_id}"}

    def sibling_traces(self, session_id):
        return ["t1", "t2"]


def test_verifier_applies_verdicts_and_refutes_missing_spans(scripted_llm, fake_response):
    reply = json.dumps({"verdicts": [{"index": 0, "verdict": "upheld", "reason": "s2 writes to notes"},
                                     {"index": 2, "verdict": "refuted", "reason": "no copying"}]})
    llm = scripted_llm([fake_response(content=f"Here you go:\n{reply}")])
    findings = [Finding("C1", ["s2"], "notes write", 0.9),
                Finding("C3", ["s404"], "made up span", 0.8),
                Finding("C4", ["s9"], "copied a note", 0.6)]
    out = Verifier(llm, "deepseek", FakeSource()).verify(trace_id="t1", session_id="r1", findings=findings)
    assert [f.verdict for f in out] == ["upheld", "refuted", "refuted"]
    assert out[1].reason == "cited span missing"
    assert findings[0].verdict is None
    sent = json.loads(llm.requests[0]["messages"][1]["content"])
    assert [i["index"] for i in sent["findings"]] == [0, 2]
    assert sent["findings"][1]["evidence"]["s9"]["output"] == "out-s9"


def test_unparseable_reply_downgrades(scripted_llm, fake_response):
    llm = scripted_llm([fake_response(content="I think it's fine")])
    out = Verifier(llm, "m", FakeSource()).verify(trace_id="t1", session_id=None,
                                                  findings=[Finding("C1", ["s2"], "x", 0.9)])
    assert out[0].verdict == "downgraded"


def test_no_findings_no_call(scripted_llm):
    llm = scripted_llm([])
    assert Verifier(llm, "m", FakeSource()).verify(trace_id="t1", session_id=None, findings=[]) == []
    assert llm.requests == []


def test_decide():
    up = Finding("C1", ["s"], "", 1, verdict="upheld")
    down = Finding("C1", ["s"], "", 1, verdict="downgraded")
    ref = Finding("C1", ["s"], "", 1, verdict="refuted")
    assert decide([up, ref]) == "verified"
    assert decide([down, ref]) == "needs_review"
    assert decide([ref]) == "dismissed" and decide([]) == "dismissed"


def test_uncertain_claim_never_stays_upheld(scripted_llm, fake_response):
    reply = json.dumps({"verdicts": [{"index": 0, "verdict": "upheld", "reason": "looks right"}]})
    llm = scripted_llm([fake_response(content=reply)])
    out = Verifier(llm, "m", FakeSource()).verify(
        trace_id="t1", session_id=None,
        findings=[Finding("C1", ["s2"], "Uncertain: maybe wrote notes", 0.5)])
    assert out[0].verdict == "downgraded"
    assert out[0].reason == "claim marked Uncertain"


def test_bad_verdict_entries_do_not_discard_good_ones(scripted_llm, fake_response):
    reply = json.dumps({"verdicts": ["junk",
                                     {"index": True, "verdict": "refuted", "reason": "bool index"},
                                     {"index": "1", "verdict": "refuted", "reason": "string index"},
                                     {"index": 0, "verdict": "upheld", "reason": "s2 writes"}]})
    llm = scripted_llm([fake_response(content=reply)])
    findings = [Finding("C1", ["s2"], "notes write", 0.9), Finding("C1", ["s2"], "second", 0.4)]
    out = Verifier(llm, "m", FakeSource()).verify(trace_id="t1", session_id=None, findings=findings)
    assert [f.verdict for f in out] == ["upheld", "downgraded"]
    assert out[1].reason == "no usable verdict"


def test_unknown_category_refuted_without_call(scripted_llm):
    llm = scripted_llm([])
    out = Verifier(llm, "m", FakeSource()).verify(trace_id="t1", session_id=None,
                                                  findings=[Finding("C99", ["s2"], "x", 0.9)])
    assert out[0].verdict == "refuted"
    assert out[0].reason == "unknown category"
    assert llm.requests == []


def test_all_findings_prerefuted_no_call(scripted_llm):
    llm = scripted_llm([])
    out = Verifier(llm, "m", FakeSource()).verify(
        trace_id="t1", session_id=None,
        findings=[Finding("C1", ["s404"], "x", 0.9), Finding("C99", ["s2"], "y", 0.9)])
    assert [f.verdict for f in out] == ["refuted", "refuted"]
    assert llm.requests == []


def test_evidence_truncated_to_3000_chars(scripted_llm, fake_response):
    class LongSource(FakeSource):
        def payload(self, trace_id, span_id):
            return {"input": "a" * 5000, "output": "b" * 3000 + "c"}

    reply = json.dumps({"verdicts": [{"index": 0, "verdict": "upheld", "reason": "ok"}]})
    llm = scripted_llm([fake_response(content=reply)])
    Verifier(llm, "m", LongSource()).verify(trace_id="t1", session_id=None,
                                            findings=[Finding("C1", ["s2"], "x", 0.9)])
    ev = json.loads(llm.requests[0]["messages"][1]["content"])["findings"][0]["evidence"]["s2"]
    assert ev["input"] == "a" * 3000
    assert ev["output"] == "b" * 3000


def test_invalid_verdict_string_downgraded(scripted_llm, fake_response):
    reply = json.dumps({"verdicts": [{"index": 0, "verdict": "maybe", "reason": "hm"}]})
    llm = scripted_llm([fake_response(content=reply)])
    out = Verifier(llm, "m", FakeSource()).verify(trace_id="t1", session_id=None,
                                                  findings=[Finding("C1", ["s2"], "x", 0.9)])
    assert out[0].verdict == "downgraded"
    assert out[0].reason == "no usable verdict"
