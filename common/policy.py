from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from common.config import REPO_ROOT

POLICY_DIR = REPO_ROOT / "policy"
_SUFFIX = re.compile(r"_\d+$")


@dataclass(frozen=True)
class Policy:
    agent: str
    tools: tuple[str, ...]
    destinations: dict[str, tuple[str, ...]]
    sensitive: tuple[str, ...]
    scope: str

    @classmethod
    def from_yaml_text(cls, text: str) -> "Policy":
        d = yaml.safe_load(text)
        return cls(
            agent=d["agent"],
            tools=tuple(d["tools"]),
            destinations={k: tuple(v or ()) for k, v in (d.get("destinations") or {}).items()},
            sensitive=tuple(d.get("sensitive") or ()),
            scope=d["scope"],
        )

    def allowed_hosts(self, tool: str) -> tuple[str, ...]:
        return self.destinations.get(tool, ())


def role_of(agent_name: str) -> str:
    return _SUFFIX.sub("", agent_name)


def policy_path(role: str, root: Path = POLICY_DIR) -> Path:
    return root / f"{role}.yaml"


def load_policy_text(role: str, root: Path = POLICY_DIR) -> str:
    return policy_path(role, root).read_text(encoding="utf-8")


def load_policy(role: str, root: Path = POLICY_DIR) -> Policy:
    return Policy.from_yaml_text(load_policy_text(role, root))


def load_catalog(root: Path = POLICY_DIR) -> dict[str, dict]:
    return yaml.safe_load((root / "catalog.yaml").read_text(encoding="utf-8"))["categories"]


def tightening_violations(old: Policy, new: Policy) -> list[str]:
    problems: list[str] = []
    if new.agent != old.agent:
        problems.append("agent renamed")
    added_tools = set(new.tools) - set(old.tools)
    if added_tools:
        problems.append(f"tools added: {sorted(added_tools)}")
    for tool, hosts in new.destinations.items():
        added = set(hosts) - set(old.destinations.get(tool, ()))
        if added:
            problems.append(f"destinations added for {tool}: {sorted(added)}")
    dropped = set(old.sensitive) - set(new.sensitive)
    if dropped:
        problems.append(f"sensitive patterns removed: {sorted(dropped)}")
    return problems
