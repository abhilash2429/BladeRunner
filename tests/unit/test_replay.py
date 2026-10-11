import json
from datetime import datetime, timedelta, timezone

import duckdb

from bench.replay import export

T0 = datetime(2026, 10, 11, 7, 0, tzinfo=timezone.utc)


def at(s):
    return T0 + timedelta(seconds=s)


def make(tmp_path):
    truth = duckdb.connect(str(tmp_path / "truth.duckdb"))
    truth.execute("CREATE TABLE runs (run_id VARCHAR, scenario VARCHAR, seed INTEGER, created_at TIMESTAMPTZ, "
                  "agents VARCHAR, canaries VARCHAR, policies VARCHAR, policy_sha VARCHAR, kind VARCHAR)")
    truth.execute("CREATE TABLE requests (id BIGINT, ts TIMESTAMPTZ, run_id VARCHAR, agent VARCHAR, step INTEGER, "
                  "tool VARCHAR, service VARCHAR, method VARCHAR, path VARCHAR, args VARCHAR, response_meta VARCHAR, "
                  "planted BOOLEAN)")
    truth.execute("INSERT INTO runs VALUES ('r1', 'planted_dev', 0, ?, '[\"research_agent_1\"]', '', '', '', 'normal'),"
                  "('rr', 'planted_dev', 0, ?, '[\"research_agent_1\"]', '', '', '', 'confirm'),"
                  "('rc', 'clean_dev', 0, ?, '[\"research_agent_1\"]', '', '', '', 'confirm')", [at(0), at(60), at(60)])
    truth.execute("INSERT INTO requests VALUES (1, ?, 'r1', 'research_agent_1', 1, 'fetch', 'docs', 'GET', "
                  "'/pages/methodology', '{\"secret\": \"x\"}', '', TRUE),"
                  "(2, ?, 'r1', 'research_agent_1', 2, 'notes_write', 'notes', 'POST', '/notes', '{}', '', FALSE)",
                  [at(1), at(2)])
    truth.close()
    wd = duckdb.connect(str(tmp_path / "wd.duckdb"))
    wd.execute("CREATE TABLE cases (case_id VARCHAR, trace_id VARCHAR, run_id VARCHAR, agent VARCHAR, status VARCHAR, "
               "source VARCHAR, flagged_at TIMESTAMPTZ, extra VARCHAR)")
    wd.execute("CREATE TABLE case_events (id BIGINT, case_id VARCHAR, ts TIMESTAMPTZ, kind VARCHAR, data VARCHAR)")
    extra = {"rerun_ids": ["rr", "rc"], "patch": {"sha": "abc", "checkpoint": "01X", "paths": ["arena/guardrails/__init__.py"],
             "why": "[AI] by Claude Code Prompt: private"}, "confirm": {"rerun_clean": True, "control_clean": True,
             "watchdog_clean": True}}
    wd.execute("INSERT INTO cases VALUES ('c1', 't1', 'r1', 'research_agent_1', 'fixed', 'both', ?, ?)",
               [at(10), json.dumps(extra)])
    finding = {"category": "C1", "claim": "wrote to notes", "evidence_span_ids": ["s2"], "confidence": 1.0}
    rows = [("opened", {"source": "native", "hits": []}), ("transition", {"from": "flagged", "to": "paused"}),
            ("findings", {"findings": [finding]}), ("findings", {"findings": [{**finding, "verdict": "upheld"}]}),
            ("transition", {"from": "patching", "to": "patched"}), ("transition", {"from": "patched", "to": "confirming"}),
            ("transition", {"from": "confirming", "to": "fixed"})]
    for i, (kind, data) in enumerate(rows):
        wd.execute("INSERT INTO case_events VALUES (?, 'c1', ?, ?, ?)", [i, at(10 + i), kind, json.dumps(data)])
    wd.close()
    span_dir = tmp_path / "spans" / "rr"
    span_dir.mkdir(parents=True)
    block = {"name": "guardrail_block", "start_time": int(at(70).timestamp() * 1e9), "attributes": {
        "neatlogs.guardrail.input": json.dumps({"tool": "notes_write", "target_host": "127.0.0.1:8102",
                                                "reason": "not allowed"})}}
    (span_dir / "research_agent_1.jsonl").write_text(json.dumps(block) + "\n", encoding="utf-8")
    return export("r1", tmp_path / "wd.duckdb", tmp_path / "truth.duckdb", tmp_path / "spans", tmp_path / "incidents")


def test_replay_carries_the_whole_loop_in_time_order(tmp_path):
    replay = make(tmp_path)
    types = [e["type"] for e in replay["events"]]
    for t in ("run.started", "agent.call", "agent.blocked", "case.opened", "case.findings", "case.verdict",
              "case.patched", "case.confirming", "case.confirmed", "case.remembered"):
        assert t in types, t
    assert [e["ts"] for e in replay["events"]] == sorted(e["ts"] for e in replay["events"])
    assert replay["runs"] == {"r1": "live", "rr": "confirm", "rc": "control"}
    call = next(e for e in replay["events"] if e["type"] == "agent.call")
    assert call["data"]["planted"] is True and "args" not in call["data"]


def test_replay_never_ships_entire_why_text(tmp_path):
    text = json.dumps(make(tmp_path))
    assert "Prompt: private" not in text and "Claude Code" not in text and '"why"' not in text
