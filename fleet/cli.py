from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx

from common.config import REPO_ROOT, RUNS_DIR, host


def agent_names(n: int) -> list[str]:
    return [f"research_agent_{i}" for i in range(1, n + 1)]


def build_agent_cmd(run_id: str, agent: str) -> list[str]:
    return [sys.executable, "-m", "arena.agent", "--run-id", run_id, "--agent", agent]


def register_run(control_url: str, scenario: str, seed: int, agents: list[str], http=None) -> str:
    http = http or httpx.Client(timeout=10)
    r = http.post(f"{control_url}/runs", json={"scenario": scenario, "seed": seed, "agents": agents})
    r.raise_for_status()
    return r.json()["run_id"]


def agent_env(run_id: str, agent: str, span_dir: Path) -> dict[str, str]:
    (span_dir / run_id).mkdir(parents=True, exist_ok=True)
    return dict(os.environ, NEATLOGS_LOG_SPANS="1", NEATLOGS_LOG_SPANS_FILE=str(span_dir / run_id / f"{agent}.jsonl"))


def run_agents(run_id: str, agents: list[str], popen=subprocess.Popen, timeout: int = 900,
               span_dir: Path = RUNS_DIR / "spans") -> list[int]:
    procs = [popen(build_agent_cmd(run_id, a), cwd=REPO_ROOT, env=agent_env(run_id, a, Path(span_dir)))
             for a in agents]
    codes = []
    for p in procs:
        try:
            codes.append(p.wait(timeout=timeout))
        except subprocess.TimeoutExpired:
            p.kill()
            codes.append(-1)
    return codes


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="fleet")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("--scenario")
    run.add_argument("--agents", type=int, default=3)
    run.add_argument("--agent-names")
    run.add_argument("--seed", type=int, default=0)
    run.add_argument("--run-id")
    run.add_argument("--record")
    args = parser.parse_args(argv)

    agents = args.agent_names.split(",") if args.agent_names else agent_names(args.agents)
    run_id = args.run_id
    if not run_id:
        if not args.scenario:
            parser.error("--scenario is required without --run-id")
        run_id = register_run(f"http://{host('control')}", args.scenario, args.seed, agents)
    print(json.dumps({"run_id": run_id, "agents": agents}), flush=True)
    if args.record:
        with open(args.record, "a", encoding="utf-8") as f:
            f.write(json.dumps({"run_id": run_id, "scenario": args.scenario, "agents": agents}) + "\n")
    codes = run_agents(run_id, agents)
    return 0 if all(c == 0 for c in codes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
