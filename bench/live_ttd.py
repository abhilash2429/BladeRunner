from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from arena.state import ArenaState
from arena.truth import label_run
from common.config import RUNS_DIR


def _ts(value) -> datetime | None:
    return datetime.fromisoformat(str(value)) if value else None


def ttd_rows(incidents_dir: Path, truth_db: Path) -> list[dict]:
    state = ArenaState(str(truth_db), read_only=True)
    rows = []
    skipped = 0
    for path in sorted(Path(incidents_dir).glob("*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))["case"]
        paused, quarantined = _ts(case.get("paused_at")), _ts(case.get("quarantined_at"))
        if not paused or not case.get("run_id"):
            continue
        label_ids = {l.request_id for l in label_run(state, case["run_id"]) if l.agent == case["agent"]}
        times = [r["ts"] for r in state.requests_for(case["run_id"]) if r["id"] in label_ids]
        if not times:
            continue
        first = min(times)
        if paused < first:
            skipped += 1
            print(f"WARNING: skipping case {case['case_id']}: paused before the first labeled request")
            continue
        rows.append({"case_id": case["case_id"], "violation_ts": str(first),
                     "pause_s": (paused - first).total_seconds(),
                     "quarantine_s": (quarantined - first).total_seconds() if quarantined else None})
    if skipped:
        print(f"WARNING: skipped {skipped} case(s) with negative pause_s")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(prog="bench.live_ttd")
    parser.add_argument("--incidents", default=str(RUNS_DIR / "incidents"))
    parser.add_argument("--truth", default=str(RUNS_DIR / "truth.duckdb"))
    args = parser.parse_args()
    print("| Case | Pause after violation (s) | Quarantine after violation (s) |")
    print("|---|---|---|")
    for r in ttd_rows(Path(args.incidents), Path(args.truth)):
        q = "n/a" if r["quarantine_s"] is None else f"{r['quarantine_s']:.1f}"
        print(f"| {r['case_id']} | {r['pause_s']:.1f} | {q} |")


if __name__ == "__main__":
    main()
