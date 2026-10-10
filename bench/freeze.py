from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from dataclasses import asdict, replace
from datetime import timedelta
from pathlib import Path

import duckdb
import httpx

from arena.state import ArenaState
from arena.truth import label_run
from common.config import REPO_ROOT, RUNS_DIR, load_env
from watchdog.neatlogs_client import NeatlogsClient, NeatlogsError
from watchdog.trace_source import Span, _is_truncated, _order, _payload_fields, normalize_span, trace_info_from


class FreezeError(RuntimeError):
    pass


def read_runs(path: Path) -> list[str]:
    ids = [json.loads(line)["run_id"] for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise FreezeError(f"duplicate run ids: {', '.join(dupes)}")
    return ids


def _incomplete(payload: dict, name: str) -> bool:
    fields = payload.get("fields")
    value = (fields if isinstance(fields, dict) else payload).get(name)
    return isinstance(value, dict) and (bool(value.get("truncated")) or value.get("available") is False)


def collect_spans(client, trace_id: str, allow_degraded: bool = False) -> tuple[list[Span], list[str]]:
    spans: list[Span] = []
    degraded: list[str] = []
    for raw in client.get_raw_spans(trace_id):
        span = normalize_span(raw)
        cut_in, cut_out = _is_truncated(raw.get("input")), _is_truncated(raw.get("output"))
        if cut_in or cut_out:
            try:
                payload = client.get_payload(trace_id, span.span_id)
                full = _payload_fields(payload)
                if any(cut and (not full[f] or _incomplete(payload, f)) for f, cut in (("input", cut_in), ("output", cut_out))):
                    raise NeatlogsError(0, "incomplete payload for truncated field")
            except (NeatlogsError, httpx.HTTPError) as e:
                if not allow_degraded:
                    status = getattr(e, "status", None)
                    raise FreezeError(f"trace {trace_id} span {span.span_id}: payload fetch failed: "
                                      f"{type(e).__name__}{f' {status}' if status else ''}") from None
                degraded.append(span.span_id)
            else:
                span = replace(span, input=full["input"] if cut_in else span.input,
                               output=full["output"] if cut_out else span.output)
        spans.append(span)
    return sorted(spans, key=_order), degraded


def _freeze_into(run_ids: list[str], client, truth_db: Path, out_dir: Path, incidents_dir: Path,
                 allow_degraded: bool) -> dict:
    state = ArenaState(str(truth_db), read_only=True)
    (out_dir / "spans").mkdir(parents=True)
    manifest: dict = {"runs": run_ids, "traces": {}, "policies": {}}
    problems: list[str] = []
    rows: list[tuple] = []

    def problem(message: str) -> None:
        if not allow_degraded:
            raise FreezeError(message)
        problems.append(message)

    for run_id in run_ids:
        run = state.run(run_id)
        if run is None:
            raise FreezeError(f"run {run_id}: not in truth db")
        for role, text in run["policies"].items():
            if manifest["policies"].setdefault(role, text) != text:
                raise FreezeError(f"run {run_id}: policy text for {role} differs from an earlier run")
        labels = label_run(state, run_id)
        since = run["created_at"] - timedelta(minutes=5)
        seen: dict[str, str] = {}
        for raw in client.list_traces(since=since, until=since + timedelta(hours=3), session_id=run_id,
                                      workflow_name="arena-agent"):
            info = trace_info_from(raw)
            if info.agent not in run["agents"]:
                problem(f"run {run_id} trace {info.trace_id}: agent {info.agent or 'untagged'} is not in the run")
                continue
            if info.agent in seen:
                problem(f"run {run_id} agent {info.agent}: extra trace {info.trace_id} (kept {seen[info.agent]})")
                continue
            seen[info.agent] = info.trace_id
            if info.finalization != "finalized" and not allow_degraded:
                raise FreezeError(f"trace {info.trace_id}: not finalized ({info.finalization})")
            spans, degraded = collect_spans(client, info.trace_id, allow_degraded)
            mismatch = info.finalization == "finalized" and info.spans_count > 0 and info.spans_count != len(spans)
            if mismatch and not allow_degraded:
                raise FreezeError(f"trace {info.trace_id}: collected {len(spans)} spans, expected {info.spans_count}")
            (out_dir / "spans" / f"{info.trace_id}.json").write_text(
                json.dumps([asdict(s) for s in spans], indent=1), encoding="utf-8")
            entry = {"run_id": run_id, "agent": info.agent, "session_id": info.session_id,
                     "tags": [t for t in info.tags if isinstance(t, str) and t.startswith("agent:")],
                     "started_at": info.started_at}
            if degraded:
                entry["degraded_spans"] = degraded
            if mismatch:
                entry["span_count_mismatch"] = True
            if info.finalization != "finalized":
                entry["not_finalized"] = True
            manifest["traces"][info.trace_id] = entry
            cats = sorted({l.category for l in labels if l.agent == info.agent})
            rows.extend((info.trace_id, run_id, info.agent, c) for c in cats) if cats else rows.append(
                (info.trace_id, run_id, info.agent, None))
        for agent in run["agents"]:
            if agent not in seen:
                problem(f"run {run_id} agent {agent}: missing trace")
    if problems:
        manifest["problems"] = problems
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    con = duckdb.connect()
    con.execute("CREATE TABLE l (trace_id VARCHAR, run_id VARCHAR, agent VARCHAR, category VARCHAR)")
    if rows:
        con.executemany("INSERT INTO l VALUES (?, ?, ?, ?)", rows)
    con.execute(f"COPY l TO '{(out_dir / 'labels.parquet').as_posix()}' (FORMAT PARQUET)")
    if Path(incidents_dir).exists():
        shutil.copytree(incidents_dir, out_dir / "incidents", dirs_exist_ok=True)
    return manifest


def freeze(run_ids: list[str], client, truth_db: Path, out_dir: Path, incidents_dir: Path,
           allow_degraded: bool = False) -> dict:
    out_dir = Path(out_dir)
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f"{out_dir.name}.tmp-", dir=out_dir.parent))
    try:
        manifest = _freeze_into(run_ids, client, truth_db, tmp, incidents_dir, allow_degraded)
        tmp.chmod(0o755)
        if out_dir.exists():
            shutil.rmtree(out_dir)
        tmp.rename(out_dir)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
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
    print(f"traces: {violating + clean} (violating {violating}, clean {clean}); freeze enforces exactly one trace per agent")


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
    if manifest.get("problems"):
        print(f"WARNING: {len(manifest['problems'])} problem(s) kept out of the gold set, see problems in the manifest")
    print(f"froze {len(manifest['traces'])} traces into {args.out}")


if __name__ == "__main__":
    main()
