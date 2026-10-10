from __future__ import annotations

import json
import re
from dataclasses import dataclass

from common.config import host
from common.policy import Policy
from watchdog.neatlogs_client import derive_key
from watchdog.trace_source import Span, tool_call


@dataclass(frozen=True)
class Rule:
    rule_id: str
    role: str
    kind: str
    tool: str = ""
    allowed: tuple[str, ...] = ()
    pattern: str = ""
    destinations: tuple[tuple[str, tuple[str, ...]], ...] = ()
    notes_host: str = ""


@dataclass(frozen=True)
class RuleHit:
    rule_id: str
    kind: str
    span_id: str


def compile_policy(policy: Policy) -> list[Rule]:
    role = policy.agent
    rules: list[Rule] = []
    empty = tuple(t for t in policy.tools if not policy.allowed_hosts(t))
    dests = tuple(sorted((t, policy.allowed_hosts(t)) for t in policy.tools))
    for tool in policy.tools:
        hosts = policy.allowed_hosts(tool)
        if hosts:
            rules.append(Rule(f"{role}:host_allowlist:{tool}", role, "host_allowlist", tool, hosts))
        else:
            rules.append(Rule(f"{role}:empty_destination:{tool}", role, "empty_destination", tool))
    rules.append(Rule(f"{role}:unlisted_tool", role, "unlisted_tool", allowed=policy.tools))
    for i, pattern in enumerate(policy.sensitive):
        rules.append(Rule(f"{role}:canary:{i}", role, "canary", pattern=pattern, destinations=dests))
    reads = tuple(t for t in empty if not t.endswith("_write"))
    if empty and "submit_answer" in policy.tools:
        rules.append(Rule(f"{role}:read_then_submit", role, "read_then_submit", "submit_answer", reads,
                          destinations=dests, notes_host=host("notes")))
    return rules


def native_spec(rule: Rule, group_id: str) -> dict | None:
    if rule.kind != "empty_destination":
        return None
    return {
        "type": "regex",
        "displayName": f"bladerunner {rule.rule_id}",
        "groupId": group_id,
        "runOn": {"frequency": "span_name", "span_names": [rule.tool]},
        "config": {"input_pattern": ".+", "output_pattern": None, "case_sensitive": False, "operator": "OR"},
    }


def emulate_native(spec: dict, span: Span) -> bool:
    if span.name not in spec["runOn"]["span_names"]:
        return False
    return re.search(spec["config"]["input_pattern"], span.input or "") is not None


def _tool(span: Span) -> dict | None:
    if span.kind != "TOOL":
        return None
    return tool_call(span)


def evaluate_span(rule: Rule, span: Span) -> bool:
    tc = _tool(span)
    if tc is None:
        return False
    if rule.kind == "empty_destination":
        return span.name == rule.tool
    if rule.kind == "host_allowlist":
        return span.name == rule.tool and tc["target_host"] not in rule.allowed
    if rule.kind == "unlisted_tool":
        return span.name not in rule.allowed
    if rule.kind == "canary":
        allowed = dict(rule.destinations).get(span.name, ())
        return bool(re.search(rule.pattern, json.dumps(tc["args"]))) and tc["target_host"] not in allowed
    return False


def _off_policy_read(rule: Rule, span: Span) -> bool:
    tc = _tool(span)
    if tc is None:
        return False
    if span.name in rule.allowed:
        return True
    allowed = dict(rule.destinations).get(span.name, ())
    return bool(allowed) and tc["target_host"] == rule.notes_host and tc["target_host"] not in allowed


def evaluate_trace(rules: list[Rule], spans: list[Span]) -> list[RuleHit]:
    ordered = spans
    hits: list[RuleHit] = []
    for rule in rules:
        if rule.kind == "read_then_submit":
            seen = False
            for s in ordered:
                if _tool(s) is not None and s.name == rule.tool and seen:
                    hits.append(RuleHit(rule.rule_id, rule.kind, s.span_id))
                    break
                if _off_policy_read(rule, s):
                    seen = True
            continue
        hits.extend(RuleHit(rule.rule_id, rule.kind, s.span_id) for s in ordered if evaluate_span(rule, s))
    return hits


def ensure_detections(client, rules: list[Rule], group_id: str, state) -> dict[str, str]:
    out: dict[str, str] = {}
    for rule in rules:
        spec = native_spec(rule, group_id)
        if spec is None:
            continue
        key = f"detection:{group_id}:{rule.rule_id}"
        detection_id = state.kv_get(key)
        if not detection_id:
            detection_id = client.create_detection(spec, idempotency_key=derive_key(f"{rule.rule_id}:{group_id}", "detection"))["id"]
            state.kv_set(key, detection_id)
        out[detection_id] = rule.rule_id
    return out
