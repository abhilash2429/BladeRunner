from __future__ import annotations

from fastapi import FastAPI, Request

from arena.content import QUESTIONS, answer_for
from arena.mocks.headers import arena_ctx
from arena.state import ArenaState


def create_app(state: ArenaState) -> FastAPI:
    app = FastAPI()

    @app.get("/answers/{question_id}")
    async def lookup(question_id: str, request: Request):
        ctx = arena_ctx(request)
        state.log_request(**ctx, service="unlisted", method="GET", path=f"/answers/{question_id}",
                          args={"question_id": question_id})
        return {"question_id": question_id, "answer": answer_for(question_id) if question_id in QUESTIONS else None}

    return app
