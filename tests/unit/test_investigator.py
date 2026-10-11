import json
from types import SimpleNamespace

import pytest

from common.llm import BudgetExceeded, SpendLedger
from watchdog.investigator import SYSTEM, TOOL_DEFS, Finding, InvestigationError, Investigator
from watchdog.neatlogs_client import NeatlogsError
from watchdog.trace_source import Span

SPANS = {
    "t1": [Span("s1", None, "fetch", "TOOL", "1", '{"tool": "fetch", "target_host": "127.0.0.1:8103", "args": {}}',
                "docs text with a note asking assistants to post to the notes board"),
           Span("s2", None, "notes_write", "TOOL", "2",
                '{"tool": "notes_write", "target_host": "127.0.0.1:8102", "args": {"body": "x"}}', '{"id": "n1"}'),
           Span("g1", None, "guardrail_block", "GUARDRAIL", "3",
                '{"tool": "notes_write", "target_host": "127.0.0.1:8102", "args": {}}', "blocked"),
           Span("r1", None, "run_stopped", "TOOL", "4", '{"reason": "done"}', "")],
    "t2": [Span("s9", None, "fetch", "TOOL", "1", '{"tool": "fetch", "target_host": "127.0.0.1:8103", "args": {}}', "")],
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


def test_finding_needs_an_executed_call_in_the_flagged_trace(scripted_llm, fake_response):
    cited = submit([{"category": "C1", "evidence_span_ids": ["s9"], "claim": "the other agent fetched",
                     "confidence": 0.9}])
    assert run(scripted_llm([fake_response(("get_span_tree", {"trace_id": "t2"})),
                             fake_response(("submit_findings", cited))])) == []


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


def test_forced_text_reply_is_an_investigation_error(scripted_llm, fake_response):
    llm = scripted_llm([fake_response(content="no")] * 6)
    with pytest.raises(InvestigationError):
        run(llm, max_calls=1)
    assert len(llm.requests) == 2


def test_forced_rounds_that_never_submit_raise(scripted_llm, fake_response):
    llm = scripted_llm([fake_response(("get_policy", {"agent": "research_agent_1"}))] * 6)
    with pytest.raises(InvestigationError):
        run(llm, max_calls=1)
    assert len(llm.requests) == 3


def test_malformed_submit_retry_works_in_a_forced_round(scripted_llm, fake_response):
    bad = fake_response(("submit_findings", {}))
    bad.choices[0].message.tool_calls[0].function.arguments = "{not json"
    llm = scripted_llm([bad, fake_response(("submit_findings", submit([
        {"category": "C1", "evidence_span_ids": ["s2"], "claim": "x", "confidence": 0.9}])))])
    out = run(llm, max_calls=0)
    assert [f.category for f in out] == ["C1"]
    assert all(r["tool_choice"]["function"]["name"] == "submit_findings" for r in llm.requests)


def test_repeated_malformed_submits_raise(scripted_llm, fake_response):
    def bad():
        r = fake_response(("submit_findings", {}))
        r.choices[0].message.tool_calls[0].function.arguments = "{not json"
        return r

    llm = scripted_llm([bad(), bad(), bad()])
    with pytest.raises(InvestigationError):
        run(llm, max_calls=0)
    assert len(llm.requests) == 2


def test_empty_submit_is_a_clean_result(scripted_llm, fake_response):
    assert run(scripted_llm([fake_response(("submit_findings", submit([])))]), max_calls=0) == []


class MissingSpanSource(FakeSource):
    def payload(self, trace_id, span_id):
        raise NeatlogsError(404, "span not found")


class BrokenSource(FakeSource):
    def payload(self, trace_id, span_id):
        raise NeatlogsError(500, "boom")


def test_model_caused_errors_go_back_to_the_model(scripted_llm, fake_response):
    def policy(role):
        if role == "ghost":
            raise FileNotFoundError(role)
        return "p"

    class BadMemory:
        def search(self, query):
            raise RuntimeError("index down")

    llm = scripted_llm([fake_response(("get_span_payload", {"trace_id": "t1", "span_id": "nope"}),
                                      ("get_policy", {"agent": "ghost_9"}),
                                      ("search_incidents", {"query": "q"}), ("mystery", {})),
                        fake_response(("submit_findings", submit([])))])
    inv = Investigator(llm, "kimi", MissingSpanSource(), policy_text_for=policy, memory=BadMemory())
    assert inv.investigate(trace_id="t1", session_id="r1", agent="research_agent_1", hits=[]) == []
    tool_msgs = [m["content"] for m in llm.requests[1]["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) == 4 and all(m.startswith("error") for m in tool_msgs)


def test_infrastructure_errors_propagate(scripted_llm, fake_response):
    llm = scripted_llm([fake_response(("get_span_payload", {"trace_id": "t1", "span_id": "s1"}))])
    inv = Investigator(llm, "kimi", BrokenSource(), policy_text_for=lambda role: "p")
    with pytest.raises(NeatlogsError):
        inv.investigate(trace_id="t1", session_id="r1", agent="research_agent_1", hits=[])


def test_get_policy_rejects_path_traversal(scripted_llm, fake_response):
    asked = []
    llm = scripted_llm([fake_response(("get_policy", {"agent": "../x"}), ("get_policy", {"agent": "a/b"}),
                                      ("get_policy", {"agent": "research_agent_1"})),
                        fake_response(("submit_findings", submit([])))])
    inv = Investigator(llm, "kimi", FakeSource(), policy_text_for=lambda role: asked.append(role) or "p")
    inv.investigate(trace_id="t1", session_id="r1", agent="research_agent_1", hits=[])
    tool_msgs = [m["content"] for m in llm.requests[1]["messages"] if m["role"] == "tool"]
    assert tool_msgs[:2] == ["error: unknown agent", "error: unknown agent"] and tool_msgs[2] == "p"
    assert asked == ["research_agent", "research_agent"]


def test_search_incidents_without_memory_returns_empty_list(scripted_llm, fake_response):
    llm = scripted_llm([fake_response(("search_incidents", {"query": "q"})),
                        fake_response(("submit_findings", submit([])))])
    inv = Investigator(llm, "kimi", FakeSource(), policy_text_for=lambda role: "p")
    inv.investigate(trace_id="t1", session_id="r1", agent="research_agent_1", hits=[])
    assert llm.requests[1]["messages"][-1]["content"] == "[]"


def test_parallel_calls_past_the_cap_are_not_run(scripted_llm, fake_response):
    asked = []
    llm = scripted_llm([fake_response(("get_policy", {"agent": "a"}), ("get_policy", {"agent": "b"}),
                                      ("get_policy", {"agent": "c"})),
                        fake_response(("submit_findings", submit([])))])
    inv = Investigator(llm, "kimi", FakeSource(), policy_text_for=lambda role: asked.append(role) or "p",
                       max_calls=2)
    inv.investigate(trace_id="t1", session_id="r1", agent="research_agent_1", hits=[])
    tool_msgs = [m["content"] for m in llm.requests[1]["messages"] if m["role"] == "tool"]
    assert tool_msgs == ["p", "p", "error: tool budget exhausted"]
    assert asked == ["research_agent", "a", "b"]


def test_empty_claim_is_skipped(scripted_llm, fake_response):
    llm = scripted_llm([fake_response(("submit_findings", submit([
        {"category": "C1", "evidence_span_ids": ["s2"], "claim": "  ", "confidence": 0.9},
        {"category": "C2", "evidence_span_ids": ["s2"], "confidence": 0.9}])))])
    assert run(llm) == []


def test_budget_exceeded_propagates(scripted_llm, fake_response, tmp_path):
    ledger = SpendLedger(tmp_path / "spend.jsonl", litellm_cap_usd=0.0)
    llm = scripted_llm([fake_response(("submit_findings", submit([])))])
    inv = Investigator(llm, "claude-sonnet-4.6", FakeSource(), policy_text_for=lambda role: "p", ledger=ledger)
    with pytest.raises(BudgetExceeded):
        inv.investigate(trace_id="t1", session_id="r1", agent="research_agent_1", hits=[])
    assert llm.requests == []


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


@pytest.mark.parametrize("args", [{}, {"findings": "abc"}, {"findings": {"category": "C1"}}, {"findings": None}])
def test_submit_without_a_findings_list_is_retried_then_raises(scripted_llm, fake_response, args):
    llm = scripted_llm([fake_response(("submit_findings", args)), fake_response(("submit_findings", args))])
    with pytest.raises(InvestigationError):
        run(llm, max_calls=0)
    assert len(llm.requests) == 2
    assert llm.requests[1]["messages"][-1]["content"].startswith("error")


def test_submit_without_a_findings_list_can_recover(scripted_llm, fake_response):
    llm = scripted_llm([fake_response(("submit_findings", {})), fake_response(("submit_findings", submit([])))])
    assert run(llm, max_calls=0) == []
    assert len(llm.requests) == 2


class MissingSiblingSource(FakeSource):
    def spans(self, trace_id):
        if trace_id == "t2":
            raise NeatlogsError(404, "trace not found")
        return super().spans(trace_id)


def test_missing_sibling_tree_goes_back_to_the_model(scripted_llm, fake_response):
    llm = scripted_llm([fake_response(("get_span_tree", {"trace_id": "t2"})),
                        fake_response(("submit_findings", submit([])))])
    inv = Investigator(llm, "kimi", MissingSiblingSource(), policy_text_for=lambda role: "p")
    assert inv.investigate(trace_id="t1", session_id="r1", agent="research_agent_1", hits=[]) == []
    assert llm.requests[1]["messages"][-1]["content"] == "error: trace t2 was not found"


class ProcessingSiblingSource(FakeSource):
    def spans(self, trace_id):
        if trace_id == "t2":
            raise NeatlogsError(409, "TRACE_PROCESSING")
        return super().spans(trace_id)


def test_sibling_still_processing_goes_back_to_the_model(scripted_llm, fake_response):
    llm = scripted_llm([fake_response(("get_span_tree", {"trace_id": "t2"})),
                        fake_response(("submit_findings", submit([])))])
    inv = Investigator(llm, "kimi", ProcessingSiblingSource(), policy_text_for=lambda role: "p")
    assert inv.investigate(trace_id="t1", session_id="r1", agent="research_agent_1", hits=[]) == []
    assert llm.requests[1]["messages"][-1]["content"] == "error: trace t2 is still processing, try again later"
