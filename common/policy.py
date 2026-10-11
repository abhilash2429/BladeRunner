from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from common.config import REPO_ROOT

POLICY_DIR = REPO_ROOT / "policy"
_SUFFIX = re.compile(r"_\d+$")
_ROLE = re.compile(r"[A-Za-z0-9_]+")


def _string_list(value: object, field: str, required: bool = False) -> tuple[str, ...]:
    if value is None:
        if required:
            raise ValueError(f"policy field '{field}' is required")
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"policy field '{field}' must be a list of strings")
    return tuple(value)


def _string(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"policy field '{field}' must be a string")
    return value


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
        if not isinstance(d, dict):
            raise ValueError("policy document must be a mapping")
        raw_dest = d.get("destinations")
        if raw_dest is None:
            raw_dest = {}
        if not isinstance(raw_dest, dict):
            raise ValueError("policy field 'destinations' must be a mapping of tool to host list")
        return cls(
            agent=_string(d.get("agent"), "agent"),
            tools=_string_list(d.get("tools"), "tools", required=True),
            destinations={
                k: _string_list(v, f"destinations.{k}") for k, v in raw_dest.items()
            },
            sensitive=_string_list(d.get("sensitive"), "sensitive"),
            scope=_string(d.get("scope"), "scope"),
        )

    def allowed_hosts(self, tool: str) -> tuple[str, ...]:
        return self.destinations.get(tool, ())


def role_of(agent_name: str) -> str:
    return _SUFFIX.sub("", agent_name)


def policy_path(role: str, root: Path = POLICY_DIR) -> Path:
    # Roles come from trace tags, which agents and anyone holding the ingest key control.
    if not _ROLE.fullmatch(role) or role == "catalog":
        raise FileNotFoundError(f"no policy for role {role!r}")
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
    if new.scope != old.scope:
        problems.append(f"scope changed from {old.scope!r} to {new.scope!r}")
    return problems
