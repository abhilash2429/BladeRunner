from __future__ import annotations

from watchdog.investigator import Finding
from watchdog.neatlogs_client import derive_key


def upheld(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.verdict == "upheld"]


def category_tags(findings: list[Finding]) -> list[str]:
    return sorted({f"cat:{f.category}" for f in upheld(findings)})[:5]


def trace_name(agent: str, findings: list[Finding]) -> str:
    cats = ",".join(sorted({f.category for f in upheld(findings)})) or "REVIEW"
    return f"[BLADERUNNER {cats}] {agent}"


class Writeback:
    def __init__(self, client, cli, state):
        self.client = client
        self.cli = cli
        self.state = state

    def tag(self, case, findings: list[Finding], status_tag: str, extra_add=(), remove=()) -> bool:
        add = ["bladerunner", status_tag, f"incident:{case.case_id}", *extra_add, *category_tags(findings)]
        try:
            self.client.patch_trace(case.trace_id, add_tags=add, remove_tags=tuple(remove),
                                    trace_name=trace_name(case.agent, findings),
                                    idempotency_key=derive_key(case.idem_key, f"tags:{status_tag}"))
        except Exception as e:
            self.state.log_event(case.case_id, "writeback_failed", {"error": f"{type(e).__name__}: {e}"[:300]})
            return False
        self.state.log_event(case.case_id, "writeback_tags", {"tags": add, "removed": list(remove)})
        return True

    def feedback(self, case, findings: list[Finding]) -> None:
        ups = upheld(findings)
        summary = "; ".join(f"{f.category}: {f.claim}" for f in ups)[:4000]
        ok, out = self.cli.trace_feedback(
            case.trace_id,
            {"rating": -100, "comment": f"Bladerunner incident {case.case_id}. {summary}",
             "issueTypes": sorted({f.category for f in ups})},
            derive_key(case.idem_key, "feedback"))
        self.state.log_event(case.case_id, "feedback_trace", {"ok": ok, "out": out[-200:]})
        for f in ups:
            for span_id in f.evidence_span_ids:
                ok, out = self.cli.span_feedback(case.trace_id, span_id,
                                                 {"vote": "down", "note": f"{f.category}: {f.claim}"[:2000]},
                                                 derive_key(case.idem_key, f"span:{span_id}"))
                self.state.log_event(case.case_id, "feedback_span", {"span_id": span_id, "ok": ok})
