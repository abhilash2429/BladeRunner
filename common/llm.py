from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING

from common.config import env

if TYPE_CHECKING:
    from openai import OpenAI

ROLES: dict[str, tuple[str, str]] = {
    "arena": ("bedrock", "minimax.minimax-m2.5"),
    "investigator": ("bedrock", "moonshotai.kimi-k2.5"),
    "investigator_fallback": ("bedrock", "zai.glm-5"),
    "verifier_dev": ("bedrock", "deepseek.v3.2"),
    "verifier_scored": ("litellm", "claude-sonnet-4.6"),
    "polish": ("litellm", "claude-opus-4.6"),
}

PRICES_PER_M: dict[str, tuple[float, float]] = {
    "minimax.minimax-m2.5": (0.30, 1.20),
    "zai.glm-4.7-flash": (0.07, 0.40),
    "moonshotai.kimi-k2.5": (0.60, 3.00),
    "zai.glm-5": (1.00, 3.20),
    "deepseek.v3.2": (0.62, 1.85),
    "claude-haiku": (1.00, 5.00),
    "claude-sonnet-4.6": (3.00, 15.00),
    "claude-opus-4.6": (5.00, 25.00),
}

_PROVIDER = {model: provider for provider, model in ROLES.values()}
_PROVIDER.update({"zai.glm-4.7-flash": "bedrock", "claude-haiku": "litellm"})


def make_client(role: str, traced: bool = True) -> tuple[OpenAI, str]:
    from openai import OpenAI

    provider, model = ROLES[role]
    if provider == "bedrock":
        client = OpenAI(base_url=env("BEDROCK_OPENAI_BASE_URL"), api_key=env("AWS_BEARER_TOKEN_BEDROCK"))
    else:
        client = OpenAI(base_url=env("LITELLM_BASE_URL"), api_key=env("LITELLM_API_KEY"))
    if traced:
        from common import tracing

        client = tracing.wrap(client)
    return client, model


class BudgetExceeded(RuntimeError):
    pass


class SpendLedger:
    def __init__(self, path: Path, litellm_cap_usd: float = 18.0):
        self.path = Path(path)
        self.cap = litellm_cap_usd
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def cost(self, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        p_in, p_out = PRICES_PER_M.get(model, (0.0, 0.0))
        return (prompt_tokens * p_in + completion_tokens * p_out) / 1_000_000

    def record(self, model: str, usage, tag: str = "") -> float:
        prompt = getattr(usage, "prompt_tokens", 0) or 0
        completion = getattr(usage, "completion_tokens", 0) or 0
        usd = self.cost(model, prompt, completion)
        row = {"ts": time.time(), "model": model, "provider": _PROVIDER.get(model, "unknown"),
               "prompt": prompt, "completion": completion, "usd": usd, "tag": tag}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        return usd

    def _rows(self):
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def total(self, provider: str | None = None, tag: str | None = None) -> float:
        return sum(r["usd"] for r in self._rows()
                   if (provider is None or r["provider"] == provider) and (tag is None or r["tag"] == tag))

    def check(self, model: str) -> None:
        if _PROVIDER.get(model) == "litellm" and self.total("litellm") >= self.cap:
            raise BudgetExceeded(f"LiteLLM spend {self.total('litellm'):.2f} reached cap {self.cap:.2f}")


def assistant_message(msg) -> dict:
    out: dict = {"role": "assistant", "content": msg.content or ""}
    if msg.tool_calls:
        out["tool_calls"] = [
            {"id": tc.id, "type": "function",
             "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}}
            for tc in msg.tool_calls
        ]
    return out


def extract_json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in text")
    return json.loads(text[start:end + 1])
