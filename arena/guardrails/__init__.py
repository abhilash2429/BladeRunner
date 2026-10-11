from __future__ import annotations

import yaml
from pathlib import Path


_POLICY_PATH = Path(__file__).resolve().parents[2] / "policy" / "research_agent.yaml"


def _load_destinations() -> dict[str, list[str]]:
    with open(_POLICY_PATH) as f:
        policy = yaml.safe_load(f)
    return policy.get("destinations", {})


_DESTINATIONS = _load_destinations()


def check_call(agent: str, tool: str, target_host: str, args: dict) -> str | None:
    allowed = _DESTINATIONS.get(tool)
    if allowed is None:
        return None
    if len(allowed) == 0:
        return f"tool {tool} is disabled"
    if target_host not in allowed:
        return f"tool {tool} not allowed to call {target_host}"
    return None


def filter_result(agent: str, tool: str, text: str) -> str:
    return text
