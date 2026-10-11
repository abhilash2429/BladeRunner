"""Build the static replay and benchmark files the site ships with.

Case events come from runs/incidents. Tool calls cited in the findings are
attached to the case. They are not given timestamps, because this export does
not include the arena request log.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "frontend" / "public"

INCIDENTS = [
    "c90be7b237f54b128327e5a1ead3ceec",
    "37fa7776126c46358bae53184c3bb1e7",
    "1ee2425c67f74babb6c87b10cb5bc6f4",
]

# Lines an OpenCode session actually wrote. The other case's provenance
# names a different agent, so it is left out of the public file.
PATCH_LINE = {
    "37fa7776126c46358bae53184c3bb1e7": "if len(allowed) == 0:",
    "1ee2425c67f74babb6c87b10cb5bc6f4": "if target_host not in allowed:",
}
WHY = {
    "37fa7776126c46358bae53184c3bb1e7": (
        "[AI] by OpenCode · zai.glm-5 · checkpoint 01M4MXVFHEZ9B2H27676DDDATN · "
        "session ses_ed62 · commit 4addfabd"
    ),
    "1ee2425c67f74babb6c87b10cb5bc6f4": (
        "[MX] by OpenCode · zai.glm-5 · checkpoint 01M4MXT5SDHXXSAKYWFF8902DT · "
        "session ses_ed62 · commit 8fcd6ae0"
    ),
}

MODELS = [
    "gpt-4o-2024-05-13",
    "claude-3-5-sonnet-20241022",
    "meta-llama_Llama-3.3-70B-Instruct",
]
MODEL_NAMES = {
    "gpt-4o-2024-05-13": "GPT-4o",
    "claude-3-5-sonnet-20241022": "Claude 3.5 Sonnet",
    "meta-llama_Llama-3.3-70B-Instruct": "Llama 3.3 70B",
}


def clip(text: str, limit: int = 500) -> str:
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def iso(ts: str) -> str:
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def cited(findings: list[dict]) -> list[dict]:
    calls: list[dict] = []
    seen: set[str] = set()
    for finding in findings:
        claim = finding.get("claim") or ""
        spans = ", ".join(finding.get("evidence_span_ids") or [])
        if any(word in claim.lower() for word in ("methodology", "8103", "instructions")):
            key = "docs"
            if key not in seen:
                seen.add(key)
                calls.append({
                    "tool": "fetch",
                    "service": "docs",
                    "detail": clip(f"Read planted methodology content. Spans: {spans}. {claim}"),
                })
        if "notes_write" in claim:
            key = "notes"
            if key not in seen:
                seen.add(key)
                calls.append({
                    "tool": "notes_write",
                    "service": "notes",
                    "detail": clip(f"notes_write to 127.0.0.1:8102. Spans: {spans}. {claim}"),
                })
    return calls


def build_replay() -> dict:
    events: list[dict] = []
    cases: dict[str, dict] = {}
    seq = 0
    agents: list[str] = []
    for case_id in INCIDENTS:
        doc = json.loads((ROOT / "runs" / "incidents" / f"{case_id}.json").read_text(encoding="utf-8"))
        case = doc["case"]
        findings = doc["findings"]
        agent = case["agent"]
        if agent not in agents:
            agents.append(agent)
        patch = case.get("extra", {}).get("patch") or {}
        confirm = case.get("extra", {}).get("confirm") or {}
        cases[case_id] = {
            "agent": agent,
            "trace_url": doc.get("trace_url") or "",
            "source": case.get("source") or "",
        }
        finding_n = 0
        pending_confirm = False
        for raw in doc["events"]:
            kind = raw["kind"]
            when = iso(raw["ts"])
            data = raw.get("data") or {}

            def emit(event_type: str, payload: dict, stamp: str = when) -> None:
                nonlocal seq
                seq += 1
                events.append({
                    "seq": seq,
                    "ts": stamp,
                    "source": "watchdog",
                    "type": event_type,
                    "run_id": case["run_id"],
                    "agent": agent,
                    "case_id": case_id,
                    "data": payload,
                })

            if kind == "opened":
                emit("case.opened", {
                    "trace_id": case["trace_id"],
                    "trace_url": doc.get("trace_url") or "",
                    "source": case.get("source") or "",
                    "rule_hits": [hit.get("rule_id") for hit in case.get("evidence") or [] if hit.get("rule_id")],
                    "cited": cited(findings),
                })
            elif kind == "transition":
                to = data.get("to")
                if to == "fixed" and pending_confirm:
                    emit("case.confirmed", {
                        "rerun_clean": bool(confirm.get("rerun_clean")),
                        "control_clean": bool(confirm.get("control_clean")),
                        "watchdog_clean": bool(confirm.get("watchdog_clean")),
                    })
                emit("case.transition", {"from": data.get("from"), "to": to})
                if to == "patched":
                    emit("case.patched", {
                        "sha": patch.get("sha") or "",
                        "paths": patch.get("paths") or [],
                        "checkpoint": patch.get("checkpoint") or "",
                        "line": PATCH_LINE.get(case_id),
                        "why": WHY.get(case_id),
                    })
                elif to == "confirming":
                    pending_confirm = True
                    emit("case.confirming", {
                        "rerun_ids": [],
                        "control_id": None,
                        "note": "Re-run ids are not in the incident file. Guardrail block spans are not in this replay.",
                    })
            elif kind == "findings":
                finding_n += 1
                packed = [{
                    "category": f.get("category"),
                    "claim": clip(f.get("claim") or ""),
                    "reason": clip(f.get("reason") or ""),
                    "verdict": f.get("verdict"),
                    "confidence": f.get("confidence"),
                    "evidence_span_ids": f.get("evidence_span_ids") or [],
                } for f in findings]
                if finding_n == 1:
                    emit("case.findings", {"findings": packed})
                else:
                    emit("case.verdict", {"findings": packed})
            elif kind in {"writeback_tags", "feedback_trace", "feedback_span"}:
                ok = data.get("ok")
                emit("case.writeback", {
                    "feedback": None if ok is None else ("ok" if ok else "not ok"),
                    "span_id": data.get("span_id"),
                })
        if finding_n < 2 and findings:
            verified = next((e for e in doc["events"] if e["kind"] == "transition" and (e.get("data") or {}).get("to") == "verified"), None)
            if verified:
                emit("case.verdict", {
                    "findings": [{
                        "category": f.get("category"),
                        "claim": clip(f.get("claim") or ""),
                        "reason": clip(f.get("reason") or ""),
                        "verdict": f.get("verdict"),
                        "confidence": f.get("confidence"),
                        "evidence_span_ids": f.get("evidence_span_ids") or [],
                    } for f in findings],
                }, iso(verified["ts"]))
    events.sort(key=lambda e: (e["ts"], e["source"], e["seq"]))
    times = [datetime.fromisoformat(e["ts"].replace("Z", "+00:00")) for e in events]
    widest = max((times[i] - times[i - 1]).total_seconds() for i in range(1, len(times)))
    if widest < 300:
        raise SystemExit(f"expected a multi-minute confirmation gap, got {widest:.0f}s")
    return {
        "run_id": "efd78263e6d2",
        "label": "11 Oct 2026, run efd78263e6d2",
        "recorded": "2026-10-11",
        "agents": agents,
        "note": "Case steps use the recorded incident timestamps. Tool calls cited in the findings are listed on the case and are not placed on the clock. Re-run guardrail blocks are not in this file.",
        "cases": cases,
        "events": events,
    }


def micro(per: dict) -> tuple[float, float]:
    tp = sum(v["tp"] for v in per.values())
    fp = sum(v["fp"] for v in per.values())
    fn = sum(v["fn"] for v in per.values())
    return tp / (tp + fp), tp / (tp + fn)


def build_exam() -> dict:
    v0 = json.loads((ROOT / "bench" / "results" / "v0.json").read_text(encoding="utf-8"))
    final = json.loads((ROOT / "bench" / "results" / "final.json").read_text(encoding="utf-8"))
    labels = pd.read_parquet(ROOT / "bench" / "frozen" / "labels.parquet")
    gold: dict[str, set[str]] = defaultdict(set)
    for row in labels.itertuples(index=False):
        if isinstance(row.category, str) and row.category:
            gold[row.trace_id].add(row.category)
    ids = list(dict.fromkeys(labels.trace_id))
    clean = sum(1 for t in ids if not gold[t])
    violating = sum(1 for t in ids if gold[t])
    if clean != 82 or violating != 20:
        raise SystemExit(f"unexpected gold split clean={clean} violating={violating}")
    if any(gold[t] != {"C1", "C3"} for t in ids if gold[t]):
        raise SystemExit("violating traces are not all C1+C3")

    def side(doc: dict, false_n: int) -> dict:
        precision, recall = micro(doc["per_category"])
        # Every violating trace includes C1, and C1 recall is 1, so each of
        # those traces raised an alarm. Misses below are category misses.
        if doc["per_category"]["C1"]["recall"] != 1:
            raise SystemExit("C1 recall changed; trace-level hits need a new derivation")
        quiet = clean - false_n
        return {
            "hit": violating,
            "false": false_n,
            "miss": 0,
            "quiet": quiet,
            "precision": round(precision, 2),
            "recall": round(recall, 2),
            "false_alarm_rate": doc["false_alarm_rate"],
            "cost_per_trace_usd": doc["cost_per_trace_usd"],
            "per_category": doc["per_category"],
        }

    v0_false = round(v0["false_alarm_rate"] * clean)
    final_false = round(final["false_alarm_rate"] * clean)
    if abs(v0["false_alarm_rate"] * clean - v0_false) > 1e-6:
        raise SystemExit("v0 false-alarm count is not an integer")
    if abs(final["false_alarm_rate"] * clean - final_false) > 1e-6:
        raise SystemExit("final false-alarm count is not an integer")
    return {
        "n": 102,
        "clean": clean,
        "violating": violating,
        "note": "All 20 violating traces are C1 and C3 together. Both checkers caught C1 on every one, so none of those traces were silent. A miss in the category table is a C3 that was not named on a trace already alarmed for C1. Squares are grouped by outcome. The result files publish totals, not a per-trace list.",
        "v0": side(v0, v0_false),
        "bladerunner": side(final, final_false),
    }


def outcome(flagged: bool, bad: bool) -> int:
    if flagged and bad:
        return 0
    if flagged and not bad:
        return 1
    if bad and not flagged:
        return 2
    return 3


def build_dojo() -> dict:
    manifest = json.loads((ROOT / "bench" / "external" / "agentdojo" / "frozen" / "manifest.json").read_text(encoding="utf-8"))
    v0 = json.loads((ROOT / "bench" / "external" / "agentdojo" / "results" / "v0.json").read_text(encoding="utf-8"))
    final = json.loads((ROOT / "bench" / "external" / "agentdojo" / "results" / "final.json").read_text(encoding="utf-8"))
    pred_v0 = v0["predictions"]
    pred_final = final["predictions"]
    dots = []
    tallies = {m: {"v0": [0, 0, 0, 0], "final": [0, 0, 0, 0]} for m in MODELS}
    for trace_id, info in manifest["traces"].items():
        model = info["tags"][0].split(":", 1)[1]
        bad = bool(info["attacked"] and info["security"])
        left = outcome(bool(pred_v0.get(trace_id)), bad)
        right = outcome(bool(pred_final.get(trace_id)), bad)
        tallies[model]["v0"][left] += 1
        tallies[model]["final"][right] += 1
        dots.append([MODELS.index(model), left, right])
    dots.sort()
    claude = "claude-3-5-sonnet-20241022"
    if tallies[claude]["v0"][1] != v0["security_label"]["by_model"][claude]["fp"]:
        raise SystemExit("claude v0 false alarms do not match the published report")
    if tallies[claude]["final"][1] != final["security_label"]["by_model"][claude]["fp"]:
        raise SystemExit("claude final false alarms do not match the published report")
    return {
        "n": len(dots),
        "label": "security",
        "models": [{"id": m, "name": MODEL_NAMES[m], "n": 286} for m in MODELS],
        "legend": ["hit", "false", "miss", "quiet"],
        "tallies": {
            MODEL_NAMES[m]: {"v0": tallies[m]["v0"], "bladerunner": tallies[m]["final"]}
            for m in MODELS
        },
        "dots": dots,
    }


def main() -> None:
    replay = build_replay()
    exam = build_exam()
    dojo = build_dojo()
    (OUT / "replay").mkdir(parents=True, exist_ok=True)
    (OUT / "data").mkdir(parents=True, exist_ok=True)
    (OUT / "replay" / "efd78263e6d2.json").write_text(json.dumps(replay, indent=1), encoding="utf-8")
    (OUT / "data" / "exam.json").write_text(json.dumps(exam, indent=1), encoding="utf-8")
    (OUT / "data" / "dojo.json").write_text(json.dumps(dojo), encoding="utf-8")
    print(f"replay events {len(replay['events'])} agents {replay['agents']}")
    print(f"exam v0 false {exam['v0']['false']} bladerunner false {exam['bladerunner']['false']}")
    claude = dojo["tallies"]["Claude 3.5 Sonnet"]
    print(f"dojo {dojo['n']} claude false {claude['v0'][1]} -> {claude['bladerunner'][1]}")


if __name__ == "__main__":
    main()
