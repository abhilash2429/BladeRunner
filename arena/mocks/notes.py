from __future__ import annotations

from fastapi import FastAPI, Request

from arena.mocks.headers import arena_ctx
from arena.state import ArenaState


def create_app(state: ArenaState) -> FastAPI:
    app = FastAPI()

    @app.get("/notes")
    async def read_notes(request: Request):
        ctx = arena_ctx(request)
        items = state.notes_for(ctx["run_id"]) if ctx["run_id"] else []
        state.log_request(**ctx, service="notes", method="GET", path="/notes", args={},
                          response_meta={"note_ids": [n["id"] for n in items]})
        return {"notes": items}

    @app.post("/notes")
    async def write_note(request: Request):
        ctx = arena_ctx(request)
        body = await request.json()
        note_id = state.add_note(ctx["run_id"], ctx["agent"] or "unknown", body.get("question_id"),
                                 body.get("answer"), str(body.get("body", "")))
        state.log_request(**ctx, service="notes", method="POST", path="/notes", args=body,
                          response_meta={"note_id": note_id})
        return {"id": note_id}

    return app
