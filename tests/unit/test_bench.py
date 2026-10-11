import json
from datetime import timedelta
from types import SimpleNamespace

import duckdb
import pytest

from arena.state import ArenaState
from bench.freeze import FreezeError, collect_spans, freeze, read_runs, read_span_log
from bench.live_ttd import ttd_rows
from bench.score import (check_coverage, cost_per_trace, frozen_policy_text, load_gold, manifest_counts,
                         markdown_table, predict_final, predict_v0, score)
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


def test_ttd_rows_skips_negative_pause(tmp_path, capsys):
    db = tmp_path / "truth.duckdb"
    st = ArenaState(str(db))
    run_id = st.register_run("planted_dev", 0, ["research_agent_1"], {"research_agent": load_policy_text("research_agent")}, "s")["run_id"]
    st.log_request(run_id=run_id, agent="research_agent_1", step=2, tool="notes_write", service="notes",
                   method="POST", path="/notes", args={"body": "x"})
    violation_ts = st.requests_for(run_id)[0]["ts"]
    st.conn.close()
    inc = tmp_path / "incidents"
    inc.mkdir()
    case = {"case_id": "early", "run_id": run_id, "agent": "research_agent_1", "paused_at": str(violation_ts - timedelta(seconds=5))}
    (inc / "early.json").write_text(json.dumps({"case": case}))
    assert ttd_rows(inc, db) == []
    assert "skipped 1 case" in capsys.readouterr().out


class FakeClient:
    def __init__(self, raw, payloads, fail=(), finalization="finalized", traces=None):
        self.raw, self.payloads, self.fail = raw, payloads, set(fail)
        self.finalization = finalization
        self.traces = traces
        self.payload_calls = []

    def list_traces(self, **kw):
        if isinstance(self.traces, dict):
            return self.traces[kw["session_id"]]
        if self.traces is not None:
            return self.traces
        return [trace("tr1", "research_agent_1", self.finalization)]

    def get_raw_spans(self, trace_id):
        return self.raw

    def get_payload(self, trace_id, span_id, fields=("input", "output")):
        self.payload_calls.append(span_id)
        if span_id in self.fail:
            raise NeatlogsError(500, "boom")
        return self.payloads[span_id]


def trace(trace_id, agent="research_agent_1", finalization="finalized", **extra):
    tags = [f"agent:{agent}"] if agent else ["misc"]
    return {"traceId": trace_id, "sessionId": "", "tags": tags, "startedAt": "2026-01-01T00:00:00Z",
            "finalizationStatus": finalization, **extra}


def wrapper(text, truncated=False):
    return {"mimeType": "application/json", "preview": text, "truncated": truncated}


def raw_span(span_id, start, inp, out, kind="LLM", parent=None):
    return {"spanId": span_id, "parentSpanId": parent, "spanName": "n" + span_id, "spanType": kind,
            "startedAt": start, "input": inp, "output": out}


def fake_client(fail=(), finalization="finalized", traces=None, payloads=None):
    raw = [raw_span("b", "2026-01-01T00:00:02Z", wrapper("short"), wrapper("short out"), "TOOL"),
           raw_span("a", "2026-01-01T00:00:01Z", wrapper("prompt pre", True), wrapper("reply pre", True))]
    default = {"a": {"fields": {"input": {"available": True, "truncated": False, "value": "FULL PROMPT"},
                                "output": {"available": True, "truncated": False, "value": "FULL REPLY"}}}}
    return FakeClient(raw, payloads if payloads is not None else default, fail, finalization, traces)


def frozen_manifest_db(tmp_path, agents=("research_agent_1",), runs=1, policy_suffix=()):
    db = tmp_path / "truth.duckdb"
    st = ArenaState(str(db))
    ids = [st.register_run("planted_dev", 0, list(agents),
                           {"research_agent": load_policy_text("research_agent") + (policy_suffix[i] if i < len(policy_suffix) else "")},
                           "s")["run_id"] for i in range(runs)]
    st.conn.close()
    return (db, ids[0]) if runs == 1 else (db, ids)


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


def run_freeze(tmp_path, client, db, run_ids, allow_degraded=False, name="out"):
    return freeze(run_ids if isinstance(run_ids, list) else [run_ids], client, db, tmp_path / name, tmp_path / "inc",
                  allow_degraded=allow_degraded, span_logs=tmp_path / "spanlogs")


def test_freeze_rejects_two_traces_for_one_agent(tmp_path):
    db, run_id = frozen_manifest_db(tmp_path)
    client = fake_client(traces=[trace("tr1"), trace("tr2")])
    with pytest.raises(FreezeError, match=f"{run_id}.*research_agent_1"):
        run_freeze(tmp_path, client, db, run_id)
    manifest = run_freeze(tmp_path, client, db, run_id, allow_degraded=True, name="out2")
    assert list(manifest["traces"]) == ["tr1"] and any("research_agent_1" in p for p in manifest["problems"])
    assert load_gold(tmp_path / "out2").keys() == {"tr1"}


def test_freeze_rejects_untagged_trace_and_never_assigns_gold(tmp_path):
    db, run_id = frozen_manifest_db(tmp_path)
    client = fake_client(traces=[trace("tr1"), trace("tr9", agent=None)])
    with pytest.raises(FreezeError, match="tr9"):
        run_freeze(tmp_path, client, db, run_id)
    manifest = run_freeze(tmp_path, client, db, run_id, allow_degraded=True, name="out2")
    assert "tr9" not in manifest["traces"] and any("tr9" in p for p in manifest["problems"])
    assert "tr9" not in load_gold(tmp_path / "out2")


def test_freeze_rejects_trace_for_agent_outside_the_run(tmp_path):
    db, run_id = frozen_manifest_db(tmp_path)
    client = fake_client(traces=[trace("tr1"), trace("tr8", agent="writer_agent_1")])
    with pytest.raises(FreezeError, match="writer_agent_1"):
        run_freeze(tmp_path, client, db, run_id)
    manifest = run_freeze(tmp_path, client, db, run_id, allow_degraded=True, name="out2")
    assert list(manifest["traces"]) == ["tr1"]


def test_freeze_rejects_missing_agent_trace(tmp_path):
    db, run_id = frozen_manifest_db(tmp_path, agents=("research_agent_1", "research_agent_2"))
    client = fake_client(traces=[trace("tr1")])
    with pytest.raises(FreezeError, match=f"{run_id}.*research_agent_2"):
        run_freeze(tmp_path, client, db, run_id)
    manifest = run_freeze(tmp_path, client, db, run_id, allow_degraded=True, name="out2")
    assert any("research_agent_2" in p and "missing" in p for p in manifest["problems"])


def test_freeze_treats_span_count_mismatch_as_degraded(tmp_path):
    db, run_id = frozen_manifest_db(tmp_path)
    client = fake_client(traces=[trace("tr1", spansCount=5)])
    with pytest.raises(FreezeError, match="tr1.*span"):
        run_freeze(tmp_path, client, db, run_id)
    manifest = run_freeze(tmp_path, client, db, run_id, allow_degraded=True, name="out2")
    assert manifest["traces"]["tr1"]["span_count_mismatch"] is True
    ok = run_freeze(tmp_path, fake_client(traces=[trace("tr1", spansCount=2)]), db, run_id, name="out3")
    assert "span_count_mismatch" not in ok["traces"]["tr1"] and "problems" not in ok


def test_freeze_refuses_conflicting_policies_even_when_degraded_allowed(tmp_path):
    db, run_ids = frozen_manifest_db(tmp_path, runs=2, policy_suffix=("", "\n# changed\n"))
    traces = {run_ids[0]: [trace("tr1")], run_ids[1]: [trace("tr2")]}
    for allow in (False, True):
        with pytest.raises(FreezeError, match="research_agent"):
            run_freeze(tmp_path, fake_client(traces=traces), db, run_ids, allow_degraded=allow, name=f"o{allow}")
    (tmp_path / "x").mkdir()
    db2, same = frozen_manifest_db(tmp_path / "x", runs=2)
    manifest = run_freeze(tmp_path, fake_client(traces={same[0]: [trace("tr1")], same[1]: [trace("tr2")]}), db2, same,
                          name="ok")
    assert set(manifest["traces"]) == {"tr1", "tr2"}


@pytest.mark.parametrize("flags", [{"truncated": True}, {"available": False}])
def test_freeze_treats_incomplete_payload_wrapper_as_degraded(tmp_path, flags):
    db, run_id = frozen_manifest_db(tmp_path)
    payloads = {"a": {"fields": {"input": {"available": True, "truncated": False, "value": "FULL PROMPT"},
                                 "output": {"available": True, "truncated": False, "value": "FULL REPLY", **flags}}}}
    with pytest.raises(FreezeError, match="tr1.*a"):
        collect_spans(fake_client(payloads=payloads), "tr1")
    manifest = run_freeze(tmp_path, fake_client(payloads=payloads), db, run_id, allow_degraded=True)
    assert manifest["traces"]["tr1"]["degraded_spans"] == ["a"]
    assert json.loads((tmp_path / "out" / "spans" / "tr1.json").read_text())[0]["output"] == "reply pre"


def test_failed_freeze_leaves_existing_target_and_no_temp_dirs(tmp_path):
    db, run_id = frozen_manifest_db(tmp_path)
    run_freeze(tmp_path, fake_client(), db, run_id)
    before = (tmp_path / "out" / "manifest.json").read_text()
    with pytest.raises(FreezeError):
        run_freeze(tmp_path, fake_client(fail={"a"}), db, run_id)
    assert (tmp_path / "out" / "manifest.json").read_text() == before
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith("out")) == ["out"]


def test_manifest_keeps_only_agent_tags(tmp_path):
    db, run_id = frozen_manifest_db(tmp_path)
    client = fake_client(traces=[dict(trace("tr1"), tags=["agent:research_agent_1", "cat:C1", "verified", "quarantined"])])
    manifest = run_freeze(tmp_path, client, db, run_id)
    assert manifest["traces"]["tr1"]["tags"] == ["agent:research_agent_1"]


def test_read_runs_rejects_duplicate_run_ids(tmp_path):
    path = tmp_path / "runs.jsonl"
    path.write_text('{"run_id": "a"}\n{"run_id": "b"}\n')
    assert read_runs(path) == ["a", "b"]
    path.write_text('{"run_id": "a"}\n{"run_id": "a"}\n')
    with pytest.raises(FreezeError, match="a"):
        read_runs(path)


def test_predict_final_counts_policy_lookup_exception(tmp_path):
    source = FrozenSource(frozen_dir(tmp_path))

    def missing(role):
        raise KeyError(role)

    errors: list = []
    inv = SimpleNamespace(investigate=lambda **kw: [])
    pred = predict_final(source, inv, SimpleNamespace(verify=lambda **kw: []), missing, errors=errors)
    assert pred == {"t_bad": set(), "t_ok": set()} and errors == ["t_bad", "t_ok"]


def test_predict_v0_counts_api_and_parse_errors(tmp_path):
    source = FrozenSource(frozen_dir(tmp_path))
    calls = []

    def create(**kw):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("api down")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="not json"))], usage=None)

    llm = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    errors: list = []
    pred = predict_v0(source, llm, "m", frozen_policy_text(source.manifest), errors=errors)
    assert pred == {"t_bad": set(), "t_ok": set()} and errors == ["t_bad", "t_ok"]


def test_predict_v0_reads_final_answer_after_reasoning_prose(tmp_path):
    source = FrozenSource(frozen_dir(tmp_path))
    seen = []
    content = ('Span notes_write args: {"question_id": "q4", "answer": "55020"} goes to a host with no destinations.\n'
               'So: {"categories": ["C1"]}')

    def create(**kw):
        seen.append(kw["max_tokens"])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=None)

    llm = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    errors: list = []
    pred = predict_v0(source, llm, "m", frozen_policy_text(source.manifest), errors=errors)
    assert pred == {"t_bad": {"C1"}, "t_ok": {"C1"}} and errors == [] and min(seen) >= 2000


def test_predict_v0_budget_exceeded_propagates(tmp_path):
    source = FrozenSource(frozen_dir(tmp_path))

    def broke(model):
        raise BudgetExceeded("cap")

    with pytest.raises(BudgetExceeded):
        predict_v0(source, None, "m", frozen_policy_text(source.manifest), SimpleNamespace(check=broke), errors=[])


def test_out_of_scope_prediction_is_not_a_false_alarm():
    s = score({"t1": {"C6"}, "t2": {"C7", "C1"}}, {"t1": set(), "t2": set()})
    assert s["false_alarm_rate"] == 0.5 and s["per_category"]["C1"]["fp"] == 1


def test_cost_per_trace_uses_only_this_invocation_prefix():
    rows = {"bench:final:old:t1": 5.0, "bench:final:new:t1": 1.0, "bench:final:new:t2": 3.0}
    ledger = SimpleNamespace(total=lambda tag: rows.get(tag, 0.0))
    assert cost_per_trace(ledger, "bench:final:new:", ["t1", "t2"]) == 2.0


def test_check_coverage_fails_loudly_on_mismatch():
    check_coverage({"a", "b"}, {"a", "b"})
    with pytest.raises(SystemExit, match="mismatch"):
        check_coverage({"a"}, {"a", "b"})


def test_manifest_counts_surface_degradation():
    manifest = {"traces": {"a": {"degraded_spans": ["s1", "s2"]}, "b": {"not_finalized": True}, "c": {}},
                "problems": ["x"]}
    assert manifest_counts(manifest) == {"degraded_spans": 2, "not_finalized": 1, "problems": 1}


def log_record(span_id, kind, name, parent, start_ns, attrs, trace_id="lt1"):
    return {"trace_id": trace_id, "span_id": span_id, "parent_span_id": parent, "name": name, "kind": kind,
            "start_time": start_ns, "end_time": start_ns + 1, "attributes": attrs}


def agent_log(tmp_path, run_id, agent="research_agent_1", records=None):
    records = records if records is not None else [
        log_record("w", "workflow", "arena-run", None, 1_767_225_600_000_000_000,
                   {"neatlogs.session.id": run_id, "neatlogs.workflow.input": '{"run_id": "x"}',
                    "neatlogs.workflow.output": '{"steps": 2}'}),
        log_record("l", "llm", "openai.chat.completions.create", "w", 1_767_225_600_001_000_000,
                   {"neatlogs.llm.input_messages.0.role": "system", "neatlogs.llm.input_messages.0.content": "rules",
                    "neatlogs.llm.input_messages.1.role": "user", "neatlogs.llm.input_messages.1.content": "q1",
                    "neatlogs.llm.output_messages.0.role": "assistant",
                    "neatlogs.llm.output_messages.0.content": "calling notes_write"}),
        log_record("t", "tool", "notes_write", "w", 1_767_225_600_002_500_000,
                   {"neatlogs.tool.input": '{"tool": "notes_write", "target_host": "127.0.0.1:8102", "args": {"a": "é"}}',
                    "neatlogs.tool.output": '"{\\"id\\": \\"n1\\"}"'}),
    ]
    path = tmp_path / "spanlogs" / run_id / f"{agent}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


def test_read_span_log_matches_the_neatlogs_span_shape(tmp_path):
    local = read_span_log(agent_log(tmp_path, "r1"))
    assert local.trace_id == "lt1" and local.session_id == "r1" and local.started_at == "2026-01-01T00:00:00.000Z"
    by_id = {s.span_id: s for s in local.spans}
    assert [s.span_id for s in local.spans] == ["w", "l", "t"]
    assert by_id["w"].kind == "WORKFLOW" and by_id["w"].input == '{"run_id":"x"}' and by_id["w"].parent_id is None
    assert by_id["l"].input == "system: rules\nuser: q1" and by_id["l"].output == "assistant: calling notes_write"
    assert by_id["t"].input == '{"tool":"notes_write","target_host":"127.0.0.1:8102","args":{"a":"é"}}'
    assert by_id["t"].output == '"{\\"id\\": \\"n1\\"}"' and by_id["t"].start == "2026-01-01T00:00:00.002Z"


@pytest.mark.parametrize("bad", ["two_traces", "no_root", "orphan", "missing"])
def test_read_span_log_rejects_incomplete_logs(tmp_path, bad):
    path = agent_log(tmp_path, "r1")
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    if bad == "two_traces":
        lines[2]["trace_id"] = "lt2"
    elif bad == "no_root":
        lines = lines[1:]
    elif bad == "orphan":
        lines[2]["parent_span_id"] = "gone"
    if bad == "missing":
        path.unlink()
    else:
        path.write_text("".join(json.dumps(r) + "\n" for r in lines), encoding="utf-8")
    assert read_span_log(path) is None


def test_freeze_falls_back_to_the_local_log_when_neatlogs_lost_the_trace(tmp_path):
    db, run_id = frozen_manifest_db(tmp_path)
    agent_log(tmp_path, run_id)
    manifest = run_freeze(tmp_path, fake_client(traces=[]), db, run_id)
    entry = manifest["traces"]["lt1"]
    assert entry["source"] == "local_log" and entry["session_id"] == run_id and entry["agent"] == "research_agent_1"
    assert entry["tags"] == ["agent:research_agent_1"] and "problems" not in manifest
    assert load_gold(tmp_path / "out").keys() == {"lt1"}
    assert [s.span_id for s in FrozenSource(tmp_path / "out").spans("lt1")] == ["w", "l", "t"]


def test_freeze_falls_back_to_the_local_log_for_a_dead_lettered_trace(tmp_path):
    db, run_id = frozen_manifest_db(tmp_path)
    agent_log(tmp_path, run_id)
    manifest = run_freeze(tmp_path, fake_client(traces=[trace("lt1", finalization="dlq")]), db, run_id)
    assert manifest["traces"]["lt1"]["source"] == "local_log" and "not_finalized" not in manifest["traces"]["lt1"]


def test_freeze_falls_back_to_the_local_log_when_neatlogs_dropped_spans(tmp_path):
    db, run_id = frozen_manifest_db(tmp_path)
    agent_log(tmp_path, run_id)
    manifest = run_freeze(tmp_path, fake_client(traces=[trace("lt1", spansCount=3)]), db, run_id)
    assert manifest["traces"]["lt1"]["source"] == "local_log"


def test_freeze_prefers_a_complete_neatlogs_trace_over_the_local_log(tmp_path):
    db, run_id = frozen_manifest_db(tmp_path)
    agent_log(tmp_path, run_id)
    manifest = run_freeze(tmp_path, fake_client(traces=[trace("tr1", spansCount=2)]), db, run_id)
    assert list(manifest["traces"]) == ["tr1"] and manifest["traces"]["tr1"]["source"] == "neatlogs"
