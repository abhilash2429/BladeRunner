from __future__ import annotations

import logging
import subprocess
import threading
import time
import uuid

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from arena.content import QUESTIONS, seed_notes
from arena.scenarios import clean_scenario_for, load_scenario
from arena.state import ArenaState
from arena.truth import label_run
from common.config import REPO_ROOT
from common.policy import load_policy_text, role_of

WATCHDOG_KEYS = frozenset({"ok", "job_id", "status", "rerun_clean", "control_clean", "rerun_ids"})
CONFIRM_RERUNS = 3
log = logging.getLogger(__name__)
BASE_TOOLS = ["fetch", "submit_answer", "notes_read", "notes_write"]


class RunRequest(BaseModel):
    scenario: str
    seed: int = 0
    agents: list[str]
    kind: str = "normal"


class AgentBody(BaseModel):
    agent: str


def policy_snapshot(agents: list[str]) -> tuple[dict[str, str], str]:
    roles = sorted({role_of(a) for a in agents})
    policies = {r: load_policy_text(r) for r in roles}
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True,
                             check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        sha = "nogit"
    return policies, sha


def _register(state: ArenaState, scenario_id: str, seed: int, agents: list[str], kind: str) -> dict:
    sc = load_scenario(scenario_id)
    policies, sha = policy_snapshot(agents)
    return state.register_run(sc.id, seed, agents, policies, sha, kind, seed_notes(sc.seed_notes, sc.questions))


def create_app(state: ArenaState, launcher, settle_seconds: float = 2.0) -> FastAPI:
    app = FastAPI()

    def require_run(run_id: str) -> dict:
        run = state.run(run_id)
        if not run:
            raise HTTPException(404, "unknown run")
        return run

    @app.post("/runs")
    def create_run(req: RunRequest):
        return _register(state, req.scenario, req.seed, req.agents, req.kind)

    @app.get("/runs/{run_id}/agent-config")
    def agent_config(run_id: str, agent: str):
        run = require_run(run_id)
        if agent not in run["agents"]:
            raise HTTPException(404, "unknown agent")
        sc = load_scenario(run["scenario"])
        qid = sc.questions[run["agents"].index(agent) % len(sc.questions)]
        exposed = BASE_TOOLS + (["lookup_cache"] if sc.expose_unlisted else [])
        role = role_of(agent)
        return {"role": role, "policy": run["policies"][role], "question_id": qid,
                "question": QUESTIONS[qid]["text"], "exposed_tools": exposed, "canary": run["canaries"][agent]}

    @app.get("/runs/{run_id}/flag")
    def flag(run_id: str, agent: str):
        return {"flag": state.get_flag(run_id, agent)}

    def flag_route(action: str, value: str):
        def handler(run_id: str, body: AgentBody):
            require_run(run_id)
            try:
                state.set_flag(run_id, body.agent, value)
            except KeyError:
                raise HTTPException(404, "unknown agent")
            return {"ok": True}

        app.post(f"/runs/{{run_id}}/{action}")(handler)

    flag_route("pause", "pause")
    flag_route("stop", "stop")
    flag_route("resume", "run")

    @app.post("/runs/{run_id}/confirm")
    def confirm(run_id: str, body: AgentBody):
        run = require_run(run_id)
        if body.agent not in run["agents"]:
            raise HTTPException(404, "unknown agent")
        sc = load_scenario(run["scenario"])
        reruns = [_register(state, sc.id, run["seed"], run["agents"], "confirm")["run_id"]
                  for _ in range(CONFIRM_RERUNS)]
        control_id = _register(state, clean_scenario_for(sc), run["seed"], run["agents"], "confirm")["run_id"]
        job_id = uuid.uuid4().hex[:12]
        job = {"status": "running", "rerun_clean": None, "control_clean": None, "rerun_ids": reruns + [control_id]}
        state.confirm_jobs[job_id] = job

        def work():
            try:
                codes = {rid: launcher.run(rid, run["agents"]) for rid in reruns + [control_id]}
                time.sleep(settle_seconds)

                def healthy(rid: str) -> bool:
                    acted = any(r["agent"] == body.agent for r in state.requests_for(rid))
                    return codes[rid] == 0 and acted

                job["rerun_clean"] = all(
                    healthy(rid) and not [l for l in label_run(state, rid) if l.agent == body.agent]
                    for rid in reruns)
                job["control_clean"] = healthy(control_id) and not label_run(state, control_id)
                job["status"] = "done"
            except Exception:
                log.exception("confirm job %s failed", job_id)
                job["rerun_clean"] = False
                job["control_clean"] = False
                job["status"] = "error"

        threading.Thread(target=work, daemon=True).start()
        return {"job_id": job_id, "rerun_ids": job["rerun_ids"]}

    @app.get("/confirm/{job_id}")
    def confirm_status(job_id: str):
        job = state.confirm_jobs.get(job_id)
        if not job:
            raise HTTPException(404, "unknown job")
        return dict(job)

    return app
