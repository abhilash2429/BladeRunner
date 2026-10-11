from __future__ import annotations

from common.policy import load_policy, role_of


def check_call(agent: str, tool: str, target_host: str, args: dict) -> str | None:
    role = role_of(agent)
    policy = load_policy(role)
    if tool not in policy.tools:
        return f"tool '{tool}' not allowed for agent '{agent}'"
    allowed = policy.allowed_hosts(tool)
    if target_host not in allowed:
        return f"host '{target_host}' not allowed for tool '{tool}' by agent '{agent}'"
    return None


def filter_result(agent: str, tool: str, text: str) -> str:
    return text
