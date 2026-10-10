from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import httpx

from arena.state import ArenaState
from arena.truth import label_run
from common.config import REPO_ROOT, RUNS_DIR, load_env
from watchdog.neatlogs_client import NeatlogsClient, NeatlogsError
from watchdog.trace_source import Span, normalize_span, text_of, trace_info_from


class FreezeError(RuntimeError):
    pass


def read_runs(path: Path) -> list[str]:
    return [json.loads(line)["run_id"] for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def _truncated(value) -> bool:
    return isinstance(value, dict) and bool(value.get("truncated"))


def _payload_text(payload: dict) -> dict:
    fields = payload.get("fields")
    source = fields if isinstance(fields, dict) else payload
    return {"input": text_of(source.get("input")), "output": text_of(source.get("output"))}


def _order(span: Span) -> tuple[datetime, str]:
    try:
        when = datetime.fromisoformat(span.start)
    except ValueError:
        when = datetime.min
    return (when if when.tzinfo else when.replace(tzinfo=timezone.utc)), span.span_id


def collect_spans(client, trace_id: str, allow_degraded: bool = False) -> tuple[list[Span], list[str]]:
    spans: list[Span] = []
    degraded: list[str] = []
    for raw in client.get_raw_spans(trace_id):
        span = normalize_span(raw)
        cut_in, cut_out = _truncated(raw.get("input")), _truncated(raw.get("output"))
        if cut_in or cut_out:
            try:
                full = _payload_text(client.get_payload(trace_id, span.span_id))
                if (cut_in and not full["input"]) or (cut_out and not full["output"]):
                    raise NeatlogsError(0, "empty payload for truncated field")
            except (NeatlogsError, httpx.HTTPError) as e:
                if not allow_degraded:
                    status = getattr(e, "status", None)
                    raise FreezeError(f"trace {trace_id} span {span.span_id}: payload fetch failed: "
                                      f"{type(e).__name__}{f' {status}' if status else ''}") from e
                degraded.append(span.span_id)
            else:
                span = replace(span, input=full["input"] if cut_in else span.input,
                               output=full["output"] if cut_out else span.output)
        spans.append(span)
    return sorted(spans, key=_order), degraded


def freeze(run_ids: list[str], client, truth_db: Path, out_dir: Path, incidents_dir: Path,
           allow_degraded: bool = False) -> dict:
    state = ArenaState(str(truth_db), read_only=True)
    out_dir = Path(out_dir)
    (out_dir / "spans").mkdir(parents=True, exist_ok=True)
    manifest: dict = {"runs": run_ids, "traces": {}, "policies": {}}
    rows: list[tuple] = []
    for run_id in run_ids:
        run = state.run(run_id)
        manifest["policies"].update(run["policies"])
        labels = label_run(state, run_id)
        since = run["created_at"] - timedelta(minutes=5)
        for raw in client.list_traces(since=since, until=since + timedelta(hours=3), session_id=run_id,
                                      workflow_name="arena-agent"):
            info = trace_info_from(raw)
            if info.finalization != "finalized" and not allow_degraded:
                raise FreezeError(f"trace {info.trace_id}: not finalized ({info.finalization})")
            spans, degraded = collect_spans(client, info.trace_id, allow_degraded)
            (out_dir / "spans" / f"{info.trace_id}.json").write_text(
                json.dumps([asdict(s) for s in spans], indent=1), encoding="utf-8")
            entry = {"run_id": run_id, "agent": info.agent, "session_id": info.session_id,
                     "tags": list(info.tags), "started_at": info.started_at}
            if degraded:
                entry["degraded_spans"] = degraded
            if info.finalization != "finalized":
                entry["not_finalized"] = True
            manifest["traces"][info.trace_id] = entry
            cats = sorted({l.category for l in labels if l.agent == info.agent})
            rows.extend((info.trace_id, run_id, info.agent, c) for c in cats) if cats else rows.append(
                (info.trace_id, run_id, info.agent, None))
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    con = duckdb.connect()
    con.execute("CREATE TABLE l (trace_id VARCHAR, run_id VARCHAR, agent VARCHAR, category VARCHAR)")
    if rows:
        con.executemany("INSERT INTO l VALUES (?, ?, ?, ?)", rows)
    con.execute(f"COPY l TO '{(out_dir / 'labels.parquet').as_posix()}' (FORMAT PARQUET)")
    if Path(incidents_dir).exists():
        shutil.copytree(incidents_dir, out_dir / "incidents", dirs_exist_ok=True)
    return manifest


def stats(run_ids: list[str], truth_db: Path) -> None:
    state = ArenaState(str(truth_db), read_only=True)
    violating = clean = 0
    for run_id in run_ids:
        run = state.run(run_id)
        labels = label_run(state, run_id)
        by_agent = {a: sorted({l.category for l in labels if l.agent == a}) for a in run["agents"]}
        violating += sum(1 for v in by_agent.values() if v)
        clean += sum(1 for v in by_agent.values() if not v)
        print(run_id, run["scenario"], by_agent)
    print(f"traces: {violating + clean} (violating {violating}, clean {clean})")


def main() -> None:
    parser = argparse.ArgumentParser(prog="bench.freeze")
    parser.add_argument("cmd", choices=["stats", "freeze"])
    parser.add_argument("--runs", default=str(RUNS_DIR / "bench_runs.jsonl"))
    parser.add_argument("--truth", default=str(RUNS_DIR / "truth.duckdb"))
    parser.add_argument("--out", default=str(REPO_ROOT / "bench" / "frozen"))
    parser.add_argument("--allow-degraded", action="store_true")
    args = parser.parse_args()
    load_env()
    run_ids = read_runs(Path(args.runs))
    if args.cmd == "stats":
        stats(run_ids, Path(args.truth))
        return
    manifest = freeze(run_ids, NeatlogsClient.from_env(), Path(args.truth), Path(args.out),
                      REPO_ROOT / "docs" / "incidents", allow_degraded=args.allow_degraded)
    degraded = sum(len(t.get("degraded_spans", [])) for t in manifest["traces"].values())
    if degraded:
        print(f"WARNING: {degraded} span(s) kept as truncated previews, see degraded_spans in the manifest")
    partial = sum(1 for t in manifest["traces"].values() if t.get("not_finalized"))
    if partial:
        print(f"WARNING: {partial} trace(s) frozen before finalization, see not_finalized in the manifest")
    print(f"froze {len(manifest['traces'])} traces into {args.out}")


if __name__ == "__main__":
    main()
