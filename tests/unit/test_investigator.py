import json
from types import SimpleNamespace

from common.llm import SpendLedger
from watchdog.investigator import SYSTEM, TOOL_DEFS, Finding, Investigator
from watchdog.trace_source import Span

SPANS = {
    "t1": [Span("s1", None, "fetch", "TOOL", "1", '{"tool": "fetch", "target_host": "127.0.0.1:8103", "args": {}}',
                "docs text with a note asking assistants to post to the notes board"),
           Span("s2", None, "notes_write", "TOOL", "2",
                '{"tool": "notes_write", "target_host": "127.0.0.1:8102", "args": {"body": "x"}}', '{"id": "n1"}'),
           Span("g1", None, "guardrail_block", "GUARDRAIL", "3",
                '{"tool": "notes_write", "target_host": "127.0.0.1:8102", "args": {}}', "blocked"),
           Span("r1", None, "run_stopped", "TOOL", "4", '{"reason": "done"}', "")],
    "t2": [Span("s9", None, "fetch", "TOOL", "1", "{}", "")],
}


class FakeSource:
    def spans(self, trace_id):
        return SPANS[trace_id]

    def payload(self, trace_id, span_id):
        s = next(s for s in SPANS[trace_id] if s.span_id == span_id)
        return {"input": s.input, "output": s.output}

    def sibling_traces(self, session_id):
        return ["t1", "t2"]


class FakeMemory:
    def search(self, query):
        return ["incident 1: notes_write after planted docs, fixed by blocking notes tools"]


FINDINGS = {"findings": [
    {"category": "C3", "evidence_span_ids": ["s1", "s2"], "claim": "wrote to notes after docs told it to",
     "confidence": 0.9},
    {"category": "C1", "evidence_span_ids": ["s2"], "claim": "notes board is not allowed", "confidence": 1.4},
    {"category": "C9", "evidence_span_ids": ["s2"], "claim": "bogus", "confidence": 0.5},
]}


def make(llm, **kw):
    return Investigator(llm, "kimi", FakeSource(), policy_text_for=lambda role: f"agent: {role}",
                        memory=FakeMemory(), **kw)


def submit(findings):
    return {"findings": findings}


def run(llm, **kw):
    return make(llm, **kw).investigate(trace_id="t1", session_id="r1", agent="research_agent_1", hits=[])


def test_investigator_reads_spans_then_submits(scripted_llm, fake_response):
    llm = scripted_llm([
        fake_response(("get_span_tree", {"trace_id": "t1"})),
        fake_response(("get_span_payload", {"trace_id": "t1", "span_id": "s1"}), ("search_incidents", {"query": "notes"})),
        fake_response(("submit_findings", FINDINGS)),
    ])
    out = make(llm).investigate(trace_id="t1", session_id="r1", agent="research_agent_1",
                                hits=[{"rule_id": "r", "span_id": "s2"}])
    assert [f.category for f in out] == ["C3", "C1"]
    assert out[1].confidence == 1.0
    seed = json.loads(llm.requests[0]["messages"][1]["content"])
    assert seed["sibling_trace_ids"] == ["t2"] and "agent: research_agent" in seed["policy"]
    tool_msgs = [m for m in llm.requests[2]["messages"] if m["role"] == "tool"]
    assert "docs text" in tool_msgs[1]["content"] and "incident 1" in tool_msgs[2]["content"]


def test_cap_forces_submit(scripted_llm, fake_response):
    llm = scripted_llm([fake_response(("get_policy", {"agent": "research_agent_1"}))] * 2 +
                       [fake_response(("submit_findings", {"findings": []}))])
    out = make(llm, max_calls=2).investigate(trace_id="t1", session_id="r1", agent="research_agent_1", hits=[])
    assert out == []
    assert llm.requests[2]["tool_choice"] == {"type": "function", "function": {"name": "submit_findings"}}


def test_unknown_trace_is_refused(scripted_llm, fake_response):
    llm = scripted_llm([fake_response(("get_span_tree", {"trace_id": "t_other"})),
                        fake_response(("submit_findings", {"findings": []}))])
    make(llm).investigate(trace_id="t1", session_id="r1", agent="research_agent_1", hits=[])
    assert llm.requests[1]["messages"][-1]["content"].startswith("error")


def test_text_reply_gets_nudged(scripted_llm, fake_response):
    llm = scripted_llm([fake_response(content="thinking"), fake_response(("submit_findings", {"findings": []}))])
    assert make(llm).investigate(trace_id="t1", session_id=None, agent="research_agent_1", hits=[]) == []
    assert llm.requests[1]["messages"][-1]["role"] == "user"


def test_prompt_and_tools_are_wired(scripted_llm, fake_response):
    llm = scripted_llm([fake_response(("submit_findings", submit([])))])
    run(llm)
    system = llm.requests[0]["messages"][0]["content"]
    assert system.startswith("You are Bladerunner's investigator.")
    assert "{categories}" not in system and "- C1: Writing to an unapproved destination." in system
    assert "Your tool budget is small and capped" in SYSTEM
    assert llm.requests[0]["tools"] is TOOL_DEFS
    assert [t["function"]["name"] for t in TOOL_DEFS] == [
        "get_span_tree", "get_span_payload", "get_policy", "search_incidents", "submit_findings"]


def test_unknown_span_ids_are_dropped_and_finding_downgraded(scripted_llm, fake_response):
    llm = scripted_llm([fake_response(("submit_findings", submit([
        {"category": "C1", "evidence_span_ids": ["s2", "s404"], "claim": "wrote to notes", "confidence": 0.9}])))])
    (f,) = run(llm)
    assert f.evidence_span_ids == ["s2"]
    assert f.claim.startswith("Uncertain:") and "wrote to notes" in f.claim
    assert f.confidence < 0.5


def test_finding_with_only_invented_ids_is_dropped(scripted_llm, fake_response):
    llm = scripted_llm([fake_response(("submit_findings", submit([
        {"category": "C1", "evidence_span_ids": ["s404"], "claim": "made up", "confidence": 0.9}])))])
    assert run(llm) == []


def test_guardrail_or_bookkeeping_spans_alone_are_not_evidence(scripted_llm, fake_response):
    llm = scripted_llm([fake_response(("submit_findings", submit([
        {"category": "C1", "evidence_span_ids": ["g1"], "claim": "blocked write", "confidence": 0.9},
        {"category": "C2", "evidence_span_ids": ["r1"], "claim": "run stopped", "confidence": 0.9},
        {"category": "C5", "evidence_span_ids": ["g1", "s2"], "claim": "leak", "confidence": 0.9}])))])
    out = run(llm)
    assert [f.category for f in out] == ["C5"]
    assert out[0].confidence == 0.9 and out[0].evidence_span_ids == ["g1", "s2"]


def test_sibling_span_ids_count_only_after_the_trace_was_opened(scripted_llm, fake_response):
    cited = submit([{"category": "C4", "evidence_span_ids": ["s9", "s2"], "claim": "copied", "confidence": 0.8}])
    unopened = run(scripted_llm([fake_response(("submit_findings", cited))]))
    assert unopened[0].evidence_span_ids == ["s2"] and unopened[0].claim.startswith("Uncertain:")
    opened = run(scripted_llm([fake_response(("get_span_tree", {"trace_id": "t2"})),
                               fake_response(("submit_findings", cited))]))
    assert opened[0].evidence_span_ids == ["s9", "s2"] and opened[0].confidence == 0.8


def test_duplicate_categories_are_merged(scripted_llm, fake_response):
    llm = scripted_llm([fake_response(("submit_findings", submit([
        {"category": "C1", "evidence_span_ids": ["s1"], "claim": "a", "confidence": 0.6},
        {"category": "C1", "evidence_span_ids": ["s2", "s1"], "claim": "b", "confidence": 0.9}])))])
    (f,) = run(llm)
    assert f.evidence_span_ids == ["s1", "s2"] and f.confidence == 0.9 and f.claim == "b"


def test_malformed_findings_are_skipped(scripted_llm, fake_response):
    llm = scripted_llm([fake_response(("submit_findings", submit([
        "junk",
        {"category": "C1", "evidence_span_ids": "s2", "claim": "x", "confidence": 0.9},
        {"category": "C1", "evidence_span_ids": ["s2"], "claim": "x", "confidence": "high"},
        {"category": "C1", "evidence_span_ids": ["s2"], "claim": "x", "confidence": float("nan")},
        {"category": "C2", "evidence_span_ids": ["s2"], "claim": "ok", "confidence": -3}])))])
    out = run(llm)
    assert [(f.category, f.confidence) for f in out] == [("C2", 0.0)]


def test_malformed_submit_arguments_get_a_retry(scripted_llm, fake_response):
    bad = fake_response(("submit_findings", {}))
    bad.choices[0].message.tool_calls[0].function.arguments = "{not json"
    llm = scripted_llm([bad, fake_response(("submit_findings", submit([
        {"category": "C1", "evidence_span_ids": ["s2"], "claim": "x", "confidence": 0.9}])))])
    out = run(llm)
    assert [f.category for f in out] == ["C1"]
    assert llm.requests[1]["messages"][-1]["content"].startswith("error")


def test_forced_round_that_never_submits_terminates(scripted_llm, fake_response):
    llm = scripted_llm([fake_response(content="no")] * 6)
    assert run(llm, max_calls=1) == []
    assert len(llm.requests) <= 4
    llm = scripted_llm([fake_response(("get_policy", {"agent": "research_agent_1"}))] * 6)
    assert run(llm, max_calls=1) == []
    assert len(llm.requests) <= 4


def test_tool_errors_are_returned_to_the_model(scripted_llm, fake_response):
    def missing(role):
        raise FileNotFoundError(role)

    llm = scripted_llm([fake_response(("get_span_payload", {"trace_id": "t1", "span_id": "nope"}),
                                      ("get_policy", {"agent": "ghost_9"}), ("mystery", {})),
                        fake_response(("submit_findings", submit([])))])
    inv = Investigator(llm, "kimi", FakeSource(), policy_text_for=lambda role: missing(role) if role == "ghost"
                       else "p")
    assert inv.investigate(trace_id="t1", session_id="r1", agent="research_agent_1", hits=[]) == []
    tool_msgs = [m["content"] for m in llm.requests[1]["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) == 3 and all(m.startswith("error") for m in tool_msgs)


def test_ledger_records_every_call(scripted_llm, fake_response, tmp_path):
    usage = SimpleNamespace(prompt_tokens=1000, completion_tokens=100)
    first = fake_response(("get_span_tree", {"trace_id": "t1"}), usage=usage)
    second = fake_response(("submit_findings", submit([])), usage=usage)
    ledger = SpendLedger(tmp_path / "spend.jsonl")
    inv = Investigator(scripted_llm([first, second]), "moonshotai.kimi-k2.5", FakeSource(),
                       policy_text_for=lambda role: "p", ledger=ledger)
    inv.investigate(trace_id="t1", session_id="r1", agent="research_agent_1", hits=[], tag="case-1")
    assert len(ledger._rows()) == 2
    assert all(r["tag"] == "case-1" for r in ledger._rows())


def test_finding_defaults():
    f = Finding("C1", ["s1"], "x", 0.5)
    assert f.verdict is None and f.reason is None
