"""Benchmark results for the site, from bench/results and bench/external/agentdojo/results."""
from __future__ import annotations

import json
from pathlib import Path

from common.config import REPO_ROOT

SCORED = ("C1", "C2", "C3", "C4", "C5")


def _held_out(results: Path) -> dict:
    out = {}
    for mode in ("v0", "final"):
        r = json.loads((results / f"{mode}.json").read_text(encoding="utf-8"))
        per = r["per_category"]
        tp, fp, fn = (sum(per[c][k] for c in SCORED) for k in ("tp", "fp", "fn"))
        out[mode] = {"precision": tp / (tp + fp) if tp + fp else None, "recall": tp / (tp + fn) if tp + fn else None,
                     "false_alarm_rate": r["false_alarm_rate"], "cost_per_trace_usd": r["cost_per_trace_usd"],
                     "tp": tp, "fp": fp, "fn": fn,
                     "per_category": {c: {k: per[c][k] for k in ("precision", "recall", "tp", "fp", "fn")}
                                      for c in SCORED}}
    return {"n_traces": 102, "violating": 20, "clean": 82, "note": "aggregate counts only; per-trace predictions "
            "were not saved for this run", "modes": out}


def _agentdojo(root: Path) -> dict:
    manifest = json.loads((root / "frozen" / "manifest.json").read_text(encoding="utf-8"))
    preds = {m: json.loads((root / "results" / f"{m}.json").read_text(encoding="utf-8")) for m in ("v0", "final")}
    runs = []
    for tid, e in manifest["traces"].items():
        tags = dict(t.split(":", 1) for t in e["tags"])
        runs.append({"id": tid[:12], "model": tags["model"], "suite": tags["suite"], "attacked": e["attacked"],
                     "acted": e["acted"], "security": bool(e["attacked"] and e.get("security")),
                     "v0": bool(preds["v0"]["predictions"].get(tid)),
                     "bladerunner": bool(preds["final"]["predictions"].get(tid))})
    summary = {m: {label: {k: v for k, v in preds[m][label].items() if k != "by_model"}
                   for label in ("acted_label", "security_label")} for m in preds}
    for m in preds:
        summary[m]["cost_per_trace_usd"] = preds[m]["cost_per_trace_usd"]
    return {"source": manifest["source"], "attack": manifest["attack"], "models": manifest["models"],
            "summary": summary, "runs": runs}


def main() -> None:
    out = REPO_ROOT / "site" / "data"
    out.mkdir(parents=True, exist_ok=True)
    data = {"held_out": _held_out(REPO_ROOT / "bench" / "results"),
            "agentdojo": _agentdojo(REPO_ROOT / "bench" / "external" / "agentdojo")}
    (out / "benchmarks.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    print(f"wrote {out / 'benchmarks.json'}: {len(data['agentdojo']['runs'])} AgentDojo runs")


if __name__ == "__main__":
    main()
