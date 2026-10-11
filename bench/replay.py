"""Export a finished run as a replay file for the site, in the event schema of docs/frontend/proposal.md.

Reads, read-only and after the run: the arena's truth.duckdb (runs, agent requests), the watchdog's
database (cases, case events), local span logs (guardrail blocks) and incident reports. Never ships
`entire why` text, tool arguments or anything that redact() would hide.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from common.config import REPO_ROOT, RUNS_DIR
from watchdog.actions.patch import redact

TEXT_LIMIT = 500
FAILED = {"pause_failed", "resume_failed", "investigation_failed", "investigation_deferred", "verification_failed",
          "stop_failed", "confirm_check_failed", "confirm_deferred", "error", "escalation_failed", "unknown_role",
          "writeback_failed", "memory_failed"}


def _iso(ts) -> str:
    if isinstance(ts, (int, float)):
        ts = datetime.fromtimestamp(ts / 1e9, tz=timezone.utc)
    return ts.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _clean(value):
    if isinstance(value, str):
        return redact(value)[:TEXT_LIMIT]
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def _event(ts, source: str, type_: str, run_id=None, agent=None, case_id=None, **data) -> dict:
    return {"ts": _iso(ts), "source": source, "type": type_, "run_id": run_id, "agent": agent,
            "case_id": case_id, "data": _clean(data)}


def _findings(rows) -> list[dict]:
    return [{k: f.get(k) for k in ("category", "claim", "evidence_span_ids", "confidence", "verdict", "reason")}
            for f in rows or []]


def _blocks(spans_dir: Path, run_id: str, kind: str) -> list[dict]:
    out = []
    for log in sorted((spans_dir / run_id).glob("*.jsonl")):
        for line in log.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("name") != "guardrail_block":
                continue
            attrs = rec.get("attributes") or {}
            try:
                call = json.loads(attrs.get("neatlogs.guardrail.input") or "{}")
            except ValueError:
                call = {}
            out.append(_event(rec["start_time"], "agent", "agent.blocked", run_id, log.stem, kind=kind,
                              tool=call.get("tool"), target_host=call.get("target_host"), reason=call.get("reason")))
    return out


def export(run_id: str, watchdog_db: Path, truth_db: Path, spans_dir: Path, incidents_dir: Path) -> dict:
    wd = duckdb.connect(str(watchdog_db), read_only=True)
    cases = [dict(zip(("case_id", "trace_id", "agent", "status", "source", "extra"), r)) for r in wd.execute(
        "SELECT case_id, trace_id, agent, status, source, extra FROM cases WHERE run_id = ? ORDER BY flagged_at",
        [run_id]).fetchall()]
    for c in cases:
        c["extra"] = json.loads(c["extra"] or "{}")
    reruns = [r for c in cases for r in c["extra"].get("rerun_ids") or []]

    truth = duckdb.connect(str(truth_db), read_only=True)
    events: list[dict] = []
    kinds: dict[str, str] = {}
    for rid, scenario, kind, agents, created in truth.execute(
            "SELECT run_id, scenario, kind, agents, created_at FROM runs WHERE list_contains(?, run_id)",
            [[run_id, *reruns]]).fetchall():
        kinds[rid] = "live" if rid == run_id else ("control" if scenario.startswith("clean") else "confirm")
        events.append(_event(created, "arena", "run.started", rid, scenario=scenario, kind=kinds[rid],
                              agents=json.loads(agents or "[]")))
    for ts, rid, agent, step, tool, service, method, path, planted in truth.execute(
            "SELECT ts, run_id, agent, step, tool, service, method, path, planted FROM requests "
            "WHERE list_contains(?, run_id) ORDER BY ts", [[run_id, *reruns]]).fetchall():
        events.append(_event(ts, "arena", "agent.call", rid, agent, kind=kinds.get(rid), step=step, tool=tool,
                             service=service, method=method, path=path, planted=bool(planted)))
    for rid in [run_id, *reruns]:
        events.extend(_blocks(spans_dir, rid, kinds.get(rid, "confirm")))

    summary = []
    for c in cases:
        cid, agent, extra = c["case_id"], c["agent"], c["extra"]
        seen_findings = False
        remembered_failed = False
        last_ts = None
        for ts, kind, raw in wd.execute("SELECT ts, kind, data FROM case_events WHERE case_id = ? ORDER BY id",
                                        [cid]).fetchall():
            last_ts = ts
            data = json.loads(raw or "{}")
            if kind == "opened":
                events.append(_event(ts, "watchdog", "case.opened", run_id, agent, cid, trace_id=c["trace_id"],
                                     detected_by=data.get("source"), hits=data.get("hits")))
            elif kind == "transition":
                events.append(_event(ts, "watchdog", "case.transition", run_id, agent, cid,
                                     **{"from": data.get("from"), "to": data.get("to")}))
                to = data.get("to")
                patch = extra.get("patch") or {}
                if to == "patched":
                    events.append(_event(ts, "watchdog", "case.patched", run_id, agent, cid, sha=patch.get("sha"),
                                         checkpoint=patch.get("checkpoint"), paths=patch.get("paths")))
                elif to == "confirming":
                    events.append(_event(ts, "watchdog", "case.confirming", run_id, agent, cid,
                                         rerun_ids=extra.get("rerun_ids")))
                elif to in ("fixed", "not_fixed"):
                    confirm = extra.get("confirm") or {}
                    events.append(_event(ts, "watchdog", "case.confirmed", run_id, agent, cid,
                                         **{k: confirm.get(k) for k in ("rerun_clean", "control_clean",
                                                                        "watchdog_clean", "reason")}))
            elif kind == "findings":
                type_ = "case.verdict" if seen_findings else "case.findings"
                seen_findings = True
                events.append(_event(ts, "watchdog", type_, run_id, agent, cid,
                                     findings=_findings(data.get("findings"))))
            elif kind == "writeback_tags":
                events.append(_event(ts, "watchdog", "case.writeback", run_id, agent, cid, tags=data.get("tags"),
                                     removed=data.get("removed")))
            elif kind in ("feedback_trace", "feedback_span"):
                events.append(_event(ts, "watchdog", "case.writeback", run_id, agent, cid,
                                     feedback=kind.split("_")[1], ok=bool(data.get("ok"))))
            elif kind in FAILED:
                remembered_failed |= kind == "memory_failed"
                events.append(_event(ts, "watchdog", "case.failed", run_id, agent, cid, kind=kind,
                                     error=data.get("error"), status=data.get("status")))
        if c["status"] == "fixed" and not remembered_failed and last_ts is not None:
            events.append(_event(last_ts, "watchdog", "case.remembered", run_id, agent, cid,
                                 path="project.incidents"))
        report = incidents_dir / f"{cid}.json"
        rep = json.loads(report.read_text(encoding="utf-8")) if report.is_file() else {}
        patch = extra.get("patch") or {}
        summary.append(_clean({"case_id": cid, "agent": agent, "status": c["status"], "detected_by": c["source"],
                               "trace_url": rep.get("trace_url"), "findings": _findings(rep.get("findings")),
                               "patch": {k: patch.get(k) for k in ("sha", "checkpoint", "paths", "attempts")},
                               "confirm": extra.get("confirm")}))
    events.sort(key=lambda e: (e["ts"], e["source"], e["type"]))
    for i, e in enumerate(events):
        e["seq"] = i
    started = min(e["ts"] for e in events) if events else None
    return {"schema": "bladerunner.replay/1", "run_id": run_id, "started_at": started,
            "runs": kinds, "cases": summary, "events": events}


def main() -> None:
    p = argparse.ArgumentParser(prog="bench.replay")
    p.add_argument("run_id")
    p.add_argument("--watchdog-db", default=str(RUNS_DIR / "watchdog.duckdb"))
    p.add_argument("--truth", default=str(RUNS_DIR / "truth.duckdb"))
    p.add_argument("--spans", default=str(RUNS_DIR / "spans"))
    p.add_argument("--incidents", default=str(RUNS_DIR / "incidents"))
    p.add_argument("--out", default=str(REPO_ROOT / "site" / "replay"))
    args = p.parse_args()
    replay = export(args.run_id, Path(args.watchdog_db), Path(args.truth), Path(args.spans), Path(args.incidents))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{args.run_id}.json").write_text(json.dumps(replay, indent=1, ensure_ascii=False), encoding="utf-8")
    counts = {}
    for e in replay["events"]:
        counts[e["type"]] = counts.get(e["type"], 0) + 1
    print(f"{len(replay['events'])} events, {len(replay['cases'])} cases -> {out / (args.run_id + '.json')}")
    print(json.dumps(counts))


if __name__ == "__main__":
    main()
