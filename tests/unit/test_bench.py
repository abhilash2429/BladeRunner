import json
from datetime import timedelta
from types import SimpleNamespace

import duckdb
import pytest

from arena.state import ArenaState
from bench.freeze import FreezeError, collect_spans, freeze
from bench.live_ttd import ttd_rows
from bench.score import (frozen_policy_text, load_gold, markdown_table, predict_final, predict_v0,
                         score)
from common.llm import BudgetExceeded
from common.policy import load_policy_text
from watchdog.investigator import Finding
from watchdog.neatlogs_client import NeatlogsError
from watchdog.trace_source import FrozenSource


def test_score_math():
    gold = {"t1": {"C1", "C3"}, "t2": set(), "t3": {"C4"}, "t4": set()}
    pred = {"t1": {"C1"}, "t2": {"C1"}, "t3": {"C4"}, "t4": set()}
    s = score(pred, gold)
    assert s["per_category"]["C1"] == {"tp": 1, "fp": 1, "fn": 0, "precision": 0.5, "recall": 1.0}
    assert s["per_category"]["C3"]["recall"] == 0.0 and s["per_category"]["C2"]["precision"] is None
    assert s["false_alarm_rate"] == 0.5 and s["n_traces"] == 4


def test_load_gold_from_parquet(tmp_path):
    con = duckdb.connect()
    con.execute("CREATE TABLE l (trace_id VARCHAR, run_id VARCHAR, agent VARCHAR, category VARCHAR)")
    con.execute("INSERT INTO l VALUES ('t1','r','a','C1'), ('t1','r','a','C3'), ('t2','r','b',NULL)")
    con.execute(f"COPY l TO '{(tmp_path / 'labels.parquet').as_posix()}' (FORMAT PARQUET)")
    assert load_gold(tmp_path) == {"t1": {"C1", "C3"}, "t2": set()}


def frozen_dir(tmp_path):
    (tmp_path / "spans").mkdir()
    manifest = {"traces": {
        "t_bad": {"run_id": "r1", "agent": "research_agent_1", "session_id": "r1", "tags": [], "started_at": ""},
        "t_ok": {"run_id": "r1", "agent": "research_agent_2", "session_id": "r1", "tags": [], "started_at": ""}},
        "policies": {"research_agent": load_policy_text("research_agent")}}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    bad = {"span_id": "s1", "parent_id": None, "name": "notes_write", "kind": "TOOL", "start": "1",
           "input": json.dumps({"tool": "notes_write", "target_host": "127.0.0.1:8102", "args": {}}), "output": ""}
    ok = dict(bad, name="fetch", input=json.dumps({"tool": "fetch", "target_host": "127.0.0.1:8101", "args": {}}))
    (tmp_path / "spans" / "t_bad.json").write_text(json.dumps([bad]))
    (tmp_path / "spans" / "t_ok.json").write_text(json.dumps([ok]))
    return tmp_path


def test_predict_final_only_investigates_flagged_traces(tmp_path):
    root = frozen_dir(tmp_path)
    source = FrozenSource(root)
    seen = []
    inv = SimpleNamespace(investigate=lambda **kw: seen.append(kw["trace_id"]) or [Finding("C1", ["s1"], "x", 0.9)])
    ver = SimpleNamespace(verify=lambda **kw: [Finding("C1", ["s1"], "x", 0.9, "upheld", "ok")])
    pred = predict_final(source, inv, ver, frozen_policy_text(source.manifest))
    assert pred == {"t_bad": {"C1"}, "t_ok": set()} and seen == ["t_bad"]


def test_markdown_table_has_both_modes():
    s = score({"t1": {"C1"}}, {"t1": {"C1"}})
    table = markdown_table({"v0": s, "final": s})
    assert "| C1 |" in table and "v0" in table and "final" in table and "\u2014" not in table


def test_ttd_rows(tmp_path):
    db = tmp_path / "truth.duckdb"
    st = ArenaState(str(db))
    run_id = st.register_run("planted_dev", 0, ["research_agent_1"], {"research_agent": load_policy_text("research_agent")}, "s")["run_id"]
    st.log_request(run_id=run_id, agent="research_agent_1", step=2, tool="notes_write", service="notes",
                   method="POST", path="/notes", args={"body": "x"})
    violation_ts = st.requests_for(run_id)[0]["ts"]
    st.conn.close()
    inc = tmp_path / "incidents"
    inc.mkdir()
    case = {"case_id": "c1", "run_id": run_id, "agent": "research_agent_1",
            "paused_at": str(violation_ts + timedelta(seconds=5)), "quarantined_at": str(violation_ts + timedelta(seconds=40))}
    (inc / "c1.json").write_text(json.dumps({"case": case}))
    rows = ttd_rows(inc, db)
    assert rows[0]["case_id"] == "c1"
    assert rows[0]["pause_s"] == pytest.approx(5.0, abs=0.01) and rows[0]["quarantine_s"] == pytest.approx(40.0, abs=0.01)


class FakeClient:
    def __init__(self, raw, payloads, fail=(), finalization="finalized"):
        self.raw, self.payloads, self.fail = raw, payloads, set(fail)
        self.finalization = finalization
        self.payload_calls = []

    def list_traces(self, **kw):
        return [{"traceId": "tr1", "sessionId": "", "tags": ["agent:research_agent_1"], "startedAt": "2026-01-01T00:00:00Z",
                 "finalizationStatus": self.finalization}]

    def get_raw_spans(self, trace_id):
        return self.raw

    def get_payload(self, trace_id, span_id, fields=("input", "output")):
        self.payload_calls.append(span_id)
        if span_id in self.fail:
            raise NeatlogsError(500, "boom")
        return self.payloads[span_id]


def wrapper(text, truncated=False):
    return {"mimeType": "application/json", "preview": text, "truncated": truncated}


def raw_span(span_id, start, inp, out, kind="LLM", parent=None):
    return {"spanId": span_id, "parentSpanId": parent, "spanName": "n" + span_id, "spanType": kind,
            "startedAt": start, "input": inp, "output": out}


def fake_client(fail=(), finalization="finalized"):
    raw = [raw_span("b", "2026-01-01T00:00:02Z", wrapper("short"), wrapper("short out"), "TOOL"),
           raw_span("a", "2026-01-01T00:00:01Z", wrapper("prompt pre", True), wrapper("reply pre", True))]
    payloads = {"a": {"fields": {"input": {"available": True, "truncated": False, "value": "FULL PROMPT"},
                            "output": {"available": True, "truncated": False, "value": "FULL REPLY"}}}}
    return FakeClient(raw, payloads, fail, finalization)


def frozen_manifest_db(tmp_path):
    db = tmp_path / "truth.duckdb"
    st = ArenaState(str(db))
    run_id = st.register_run("planted_dev", 0, ["research_agent_1"], {"research_agent": load_policy_text("research_agent")}, "s")["run_id"]
    st.conn.close()
    return db, run_id


def test_collect_spans_fetches_full_payload_for_truncated_only():
    client = fake_client()
    spans, degraded = collect_spans(client, "tr1")
    assert [s.span_id for s in spans] == ["a", "b"] and degraded == []
    assert spans[0].input == "FULL PROMPT" and spans[0].output == "FULL REPLY"
    assert spans[1].input == "short" and client.payload_calls == ["a"]


def test_collect_spans_raises_on_payload_failure_by_default():
    with pytest.raises(FreezeError, match="tr1.*a"):
        collect_spans(fake_client(fail={"a"}), "tr1")


def test_freeze_writes_full_spans_and_lists_degraded(tmp_path):
    db, run_id = frozen_manifest_db(tmp_path)
    with pytest.raises(FreezeError):
        freeze([run_id], fake_client(fail={"a"}), db, tmp_path / "out0", tmp_path / "inc")
    manifest = freeze([run_id], fake_client(), db, tmp_path / "out", tmp_path / "inc")
    rows = json.loads((tmp_path / "out" / "spans" / "tr1.json").read_text())
    assert rows[0]["input"] == "FULL PROMPT" and "degraded_spans" not in manifest["traces"]["tr1"]
    manifest = freeze([run_id], fake_client(fail={"a"}), db, tmp_path / "out2", tmp_path / "inc", allow_degraded=True)
    assert manifest["traces"]["tr1"]["degraded_spans"] == ["a"]
    rows = json.loads((tmp_path / "out2" / "spans" / "tr1.json").read_text())
    assert rows[0]["input"] == "prompt pre"


def test_freeze_refuses_traces_that_are_not_finalized(tmp_path):
    db, run_id = frozen_manifest_db(tmp_path)
    with pytest.raises(FreezeError, match="tr1.*not finalized"):
        freeze([run_id], fake_client(finalization="pending"), db, tmp_path / "out", tmp_path / "inc")
    manifest = freeze([run_id], fake_client(finalization="pending"), db, tmp_path / "out2", tmp_path / "inc",
                      allow_degraded=True)
    assert manifest["traces"]["tr1"]["not_finalized"] is True


def test_freeze_error_does_not_carry_the_response_body():
    with pytest.raises(FreezeError) as e:
        collect_spans(fake_client(fail={"a"}), "tr1")
    assert "boom" not in str(e.value) and "NeatlogsError" in str(e.value)


def test_predict_final_stops_on_budget(tmp_path):
    root = frozen_dir(tmp_path)
    source = FrozenSource(root)

    def broke(**kw):
        raise BudgetExceeded("cap")

    inv = SimpleNamespace(investigate=broke)
    with pytest.raises(BudgetExceeded):
        predict_final(source, inv, SimpleNamespace(verify=lambda **kw: []), frozen_policy_text(source.manifest),
                      errors=[])


def test_predict_final_counts_errors_and_continues(tmp_path):
    root = frozen_dir(tmp_path)
    source = FrozenSource(root)

    def boom(**kw):
        raise RuntimeError("llm down")

    inv = SimpleNamespace(investigate=boom)
    ver = SimpleNamespace(verify=lambda **kw: [])
    errors: list = []
    pred = predict_final(source, inv, ver, frozen_policy_text(source.manifest), errors=errors)
    assert pred == {"t_bad": set(), "t_ok": set()} and errors == ["t_bad"]


def test_predict_v0_checks_budget_and_tolerates_bad_categories(tmp_path):
    root = frozen_dir(tmp_path)
    source = FrozenSource(root)
    calls = []

    def create(**kw):
        calls.append(kw["model"])
        content = '{"categories": "C1"}' if len(calls) == 1 else '{"categories": ["C1", "C9"]}'
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=None)

    llm = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    checked = []
    ledger = SimpleNamespace(check=lambda m: checked.append(m), record=lambda *a, **k: 0.0)
    pred = predict_v0(source, llm, "moonshotai.kimi-k2.5", frozen_policy_text(source.manifest), ledger)
    assert pred == {"t_bad": set(), "t_ok": {"C1"}} and len(checked) == 2
