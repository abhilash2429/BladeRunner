from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

from arena.content import docs_page
from arena.mocks.headers import arena_ctx, scenario_for
from arena.state import ArenaState


def create_app(state: ArenaState) -> FastAPI:
    app = FastAPI()

    @app.get("/pages/{slug}")
    async def page(slug: str, request: Request):
        ctx = arena_ctx(request)
        sc = scenario_for(state, ctx["run_id"])
        text, planted = docs_page(slug, sc.docs_page if sc else "normal")
        state.log_request(**ctx, service="docs", method="GET", path=f"/pages/{slug}", args={},
                          planted=planted, response_meta={"found": text is not None})
        if text is None:
            return PlainTextResponse("not found", status_code=404)
        return PlainTextResponse(text)

    return app
