from __future__ import annotations

import hashlib
import json

from watchdog.investigator import Finding
from watchdog.neatlogs_client import derive_key

TAG_CAP = 10


def upheld(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.verdict == "upheld"]


def category_tags(findings: list[Finding]) -> list[str]:
    return sorted({f"cat:{f.category}" for f in upheld(findings)})


def trace_name(agent: str, findings: list[Finding]) -> str:
    cats = ",".join(sorted({f.category for f in upheld(findings)})) or "REVIEW"
    return f"[BLADERUNNER {cats}] {agent}"


def _payload_digest(add: list[str], remove: tuple, name: str) -> str:
    blob = json.dumps([sorted(add), sorted(remove), name], separators=(",", ":"))
    return hashlib.sha1(blob.encode()).hexdigest()[:10]


class Writeback:
    def __init__(self, client, cli, state):
        self.client = client
        self.cli = cli
        self.state = state

    def _tag_failed(self, case, error: str, status=None) -> bool:
        self.state.log_event(case.case_id, "writeback_failed", {"error": error, "status": status})
        return False

    def tag(self, case, findings: list[Finding], status_tag: str, extra_add=(), remove=()) -> bool:
        add = list(dict.fromkeys(["bladerunner", status_tag, f"incident:{case.case_id}", *extra_add,
                                  *category_tags(findings)]))
        remove = tuple(remove)
        name = trace_name(case.agent, findings)
        if len(add) > TAG_CAP:
            return self._tag_failed(case, "TooManyTags")
        key = derive_key(case.idem_key, f"tags:{status_tag}:{_payload_digest(add, remove, name)}")
        try:
            result = self.client.patch_trace(case.trace_id, add_tags=add, remove_tags=remove, trace_name=name,
                                             idempotency_key=key)
        except Exception as e:
            return self._tag_failed(case, type(e).__name__, getattr(e, "status", None))
        final = result.get("tags") if isinstance(result, dict) else None
        if isinstance(final, list) and not all(t in final for t in add):
            return self._tag_failed(case, "TagsDropped")
        self.state.log_event(case.case_id, "writeback_tags", {"tags": add, "removed": list(remove)})
        return True

    def _feedback_call(self, case, target: str, span_id, call) -> bool:
        try:
            ok, out = call()
        except Exception as e:
            data = {"target": target, "error": type(e).__name__}
            if span_id:
                data["span_id"] = span_id
            self.state.log_event(case.case_id, "feedback_failed", data)
            return False
        if target == "trace":
            self.state.log_event(case.case_id, "feedback_trace", {"ok": ok, "out": out[-200:]})
        else:
            self.state.log_event(case.case_id, "feedback_span", {"span_id": span_id, "ok": ok})
        return bool(ok)

    def feedback(self, case, findings: list[Finding]) -> bool:
        ups = upheld(findings)
        summary = "; ".join(f"{f.category}: {f.claim}" for f in ups)[:4000]
        body = {"rating": -100, "comment": f"Bladerunner incident {case.case_id}. {summary}",
                "issueTypes": sorted({f.category for f in ups})}
        all_ok = self._feedback_call(case, "trace", None, lambda: self.cli.trace_feedback(
            case.trace_id, body, derive_key(case.idem_key, "feedback")))
        notes: dict[str, list[str]] = {}
        for f in ups:
            for span_id in f.evidence_span_ids:
                notes.setdefault(span_id, []).append(f"{f.category}: {f.claim}")
        for span_id, parts in notes.items():
            note = "; ".join(dict.fromkeys(parts))[:2000]
            ok = self._feedback_call(case, "span", span_id, lambda: self.cli.span_feedback(
                case.trace_id, span_id, {"vote": "down", "note": note},
                derive_key(case.idem_key, f"span:{span_id}")))
            all_ok = ok and all_ok
        return all_ok
