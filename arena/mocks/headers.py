from __future__ import annotations

from fastapi import Request

from arena.scenarios import Scenario, load_scenario
from arena.state import ArenaState


def arena_ctx(request: Request) -> dict:
    h = request.headers
    step = h.get("x-arena-step")
    return {
        "run_id": h.get("x-arena-run"),
        "agent": h.get("x-arena-agent"),
        "step": int(step) if step and step.isdigit() else None,
        "tool": h.get("x-arena-tool"),
    }


def scenario_for(state: ArenaState, run_id: str | None) -> Scenario | None:
    run = state.run(run_id) if run_id else None
    return load_scenario(run["scenario"]) if run else None
