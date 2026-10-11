from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NamedTuple

import duckdb
import httpx

from arena.state import ArenaState
from arena.truth import label_run
from common.config import REPO_ROOT, RUNS_DIR, load_env
from watchdog.neatlogs_client import NeatlogsClient, NeatlogsError
from watchdog.trace_source import (Span, TraceInfo, _is_truncated, _order, _payload_fields, normalize_span,
                                   trace_info_from)


class FreezeError(RuntimeError):
    pass


class LocalTrace(NamedTuple):
    trace_id: str
    session_id: str | None
    started_at: str
    spans: list[Span]


def _compact(text) -> str:
    try:
        return json.dumps(json.loads(text), separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError):
        return text or ""


def _messages(attrs: dict, prefix: str) -> str:
    lines, i = [], 0
    while f"{prefix}.{i}.role" in attrs:
        lines.append(f"{attrs[f'{prefix}.{i}.role']}: {attrs.get(f'{prefix}.{i}.content') or ''}")
        i += 1
    return "\n".join(lines)


def _iso(ns: int) -> str:
    return datetime.fromtimestamp(ns // 1_000_000 / 1000, timezone.utc).isoformat(
        timespec="milliseconds").replace("+00:00", "Z")


def local_span(record: dict) -> Span:
    attrs, kind = record.get("attributes") or {}, str(record.get("kind", "")).upper()
    if kind == "LLM":
        inp = _messages(attrs, "neatlogs.llm.input_messages")
        out = _messages(attrs, "neatlogs.llm.output_messages")
    else:
        inp = _compact(attrs.get(f"neatlogs.{kind.lower()}.input"))
        out = _compact(attrs.get(f"neatlogs.{kind.lower()}.output"))
    return Span(record["span_id"], record.get("parent_span_id"), record["name"], kind, _iso(record["start_time"]),
                inp, out)


def read_span_log(path: Path) -> LocalTrace | None:
    path = Path(path)
    if not path.is_file():
        return None
    try:
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except ValueError:
        return None
    ids = {r.get("span_id") for r in records}
    roots = [r for r in records if not r.get("parent_span_id")]
    if (len({r.get("trace_id") for r in records}) != 1 or len(ids) != len(records) or len(roots) != 1
            or roots[0].get("kind") != "workflow"
            or any(r.get("parent_span_id") and r["parent_span_id"] not in ids for r in records)):
        return None
    root = roots[0]
    return LocalTrace(root["trace_id"], (root.get("attributes") or {}).get("neatlogs.session.id"),
                      _iso(root["start_time"]), sorted((local_span(r) for r in records), key=_order))


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


def _from_neatlogs(client, info: TraceInfo, allow_degraded: bool) -> tuple[list[Span], dict]:
    if info.finalization != "finalized" and not allow_degraded:
        raise FreezeError(f"trace {info.trace_id}: not finalized ({info.finalization})")
    spans, degraded = collect_spans(client, info.trace_id, allow_degraded)
    mismatch = info.finalization == "finalized" and info.spans_count > 0 and info.spans_count != len(spans)
    if mismatch and not allow_degraded:
        raise FreezeError(f"trace {info.trace_id}: collected {len(spans)} spans, expected {info.spans_count}")
    flags: dict = {}
    if degraded:
        flags["degraded_spans"] = degraded
    if mismatch:
        flags["span_count_mismatch"] = True
    if info.finalization != "finalized":
        flags["not_finalized"] = True
    return spans, flags


def _freeze_into(run_ids: list[str], client, truth_db: Path, out_dir: Path, incidents_dir: Path,
                 allow_degraded: bool, span_logs: Path) -> dict:
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
        found: dict[str, TraceInfo] = {}
        for raw in client.list_traces(since=since, until=since + timedelta(hours=3), session_id=run_id,
                                      workflow_name="arena-agent"):
            info = trace_info_from(raw)
            if info.agent not in run["agents"]:
                problem(f"run {run_id} trace {info.trace_id}: agent {info.agent or 'untagged'} is not in the run")
                continue
            if info.agent in found:
                problem(f"run {run_id} agent {info.agent}: extra trace {info.trace_id} "
                        f"(kept {found[info.agent].trace_id})")
                continue
            found[info.agent] = info
        for agent in run["agents"]:
            info = found.get(agent)
            local = read_span_log(Path(span_logs) / run_id / f"{agent}.jsonl")
            spans, flags = None, {}
            if local is not None and info is not None:
                try:
                    spans, flags = _from_neatlogs(client, info, False)
                except (FreezeError, NeatlogsError, httpx.HTTPError):
                    spans, flags = None, {}
            elif local is None:
                if info is None:
                    problem(f"run {run_id} agent {agent}: missing trace")
                    continue
                spans, flags = _from_neatlogs(client, info, allow_degraded)
            if spans is None:
                trace_id, session_id, started_at, spans = local
                source, tags = "local_log", [f"agent:{agent}"]
            else:
                trace_id, session_id, started_at, source = info.trace_id, info.session_id, info.started_at, "neatlogs"
                tags = [t for t in info.tags if isinstance(t, str) and t.startswith("agent:")]
            (out_dir / "spans" / f"{trace_id}.json").write_text(
                json.dumps([asdict(s) for s in spans], indent=1), encoding="utf-8")
            manifest["traces"][trace_id] = {"run_id": run_id, "agent": agent, "session_id": session_id,
                                            "tags": tags, "started_at": started_at, "source": source, **flags}
            cats = sorted({l.category for l in labels if l.agent == agent})
            rows.extend((trace_id, run_id, agent, c) for c in cats) if cats else rows.append(
                (trace_id, run_id, agent, None))
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
           allow_degraded: bool = False, span_logs: Path = RUNS_DIR / "spans") -> dict:
    out_dir = Path(out_dir)
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f"{out_dir.name}.tmp-", dir=out_dir.parent))
    try:
        manifest = _freeze_into(run_ids, client, truth_db, tmp, incidents_dir, allow_degraded, span_logs)
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
    parser.add_argument("--span-logs", default=str(RUNS_DIR / "spans"))
    args = parser.parse_args()
    load_env()
    run_ids = read_runs(Path(args.runs))
    if args.cmd == "stats":
        stats(run_ids, Path(args.truth))
        return
    manifest = freeze(run_ids, NeatlogsClient.from_env(), Path(args.truth), Path(args.out),
                      REPO_ROOT / "docs" / "incidents", allow_degraded=args.allow_degraded,
                      span_logs=Path(args.span_logs))
    degraded = sum(len(t.get("degraded_spans", [])) for t in manifest["traces"].values())
    if degraded:
        print(f"WARNING: {degraded} span(s) kept as truncated previews, see degraded_spans in the manifest")
    partial = sum(1 for t in manifest["traces"].values() if t.get("not_finalized"))
    if partial:
        print(f"WARNING: {partial} trace(s) frozen before finalization, see not_finalized in the manifest")
    if manifest.get("problems"):
        print(f"WARNING: {len(manifest['problems'])} problem(s) kept out of the gold set, see problems in the manifest")
    local = sum(1 for t in manifest["traces"].values() if t.get("source") == "local_log")
    if local:
        print(f"NOTE: {local} trace(s) frozen from the SDK's local span log because neatlogs lost or cut them")
    print(f"froze {len(manifest['traces'])} traces into {args.out}")


if __name__ == "__main__":
    main()
