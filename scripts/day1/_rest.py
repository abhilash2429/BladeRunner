from __future__ import annotations

import json
import uuid

import httpx

from common.config import REPO_ROOT, env, load_env

load_env()

BASE = "https://app.neatlogs.com/api/v1/public"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "neatlogs" / "recorded"


def client() -> httpx.Client:
    return httpx.Client(
        base_url=BASE,
        headers={
            "Authorization": f"Bearer {env('NEATLOGS_TOKEN')}",
            "x-project-id": env("NEATLOGS_PROJECT_ID"),
        },
        timeout=30,
    )


def idem() -> str:
    return str(uuid.uuid4())


def save(name: str, data) -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / f"{name}.json").write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
