from __future__ import annotations

import asyncio

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from arena.content import STATS
from arena.mocks.headers import arena_ctx, scenario_for
from arena.state import ArenaState


def create_app(state: ArenaState) -> FastAPI:
    app = FastAPI()

    async def degraded(ctx: dict, path: str, args: dict) -> JSONResponse | None:
        sc = scenario_for(state, ctx["run_id"])
        if not sc or sc.data_api != "slow":
            return None
        await asyncio.sleep(4)
        if state.bump(f"slow:{ctx['run_id']}:{ctx['agent']}") % 2 == 1:
            state.log_request(**ctx, service="data", method="GET", path=path, args=args,
                              response_meta={"status": 503})
            return JSONResponse({"error": "temporarily unavailable"}, status_code=503)
        return None

    @app.get("/datasets")
    async def list_datasets(request: Request):
        ctx = arena_ctx(request)
        if (resp := await degraded(ctx, "/datasets", {})) is not None:
            return resp
        state.log_request(**ctx, service="data", method="GET", path="/datasets", args={})
        return {"datasets": sorted(STATS)}

    @app.get("/datasets/{name}")
    async def get_value(name: str, request: Request, county: str = "", year: int = 0):
        ctx = arena_ctx(request)
        args = {"name": name, "county": county, "year": year}
        path = f"/datasets/{name}"
        if (resp := await degraded(ctx, path, args)) is not None:
            return resp
        value = STATS.get(name, {}).get(county, {}).get(str(year))
        state.log_request(**ctx, service="data", method="GET", path=path, args=args,
                          response_meta={"found": value is not None})
        if value is None:
            return JSONResponse({"error": "not found", "hint": "check the county name and year"}, status_code=404)
        return {"dataset": name, "county": county, "year": year, "value": value}

    @app.post("/submit")
    async def submit(request: Request):
        ctx = arena_ctx(request)
        body = await request.json()
        state.log_request(**ctx, service="data", method="POST", path="/submit", args=body)
        return {"received": True}

    return app
