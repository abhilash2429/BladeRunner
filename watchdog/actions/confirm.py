from __future__ import annotations

import time

from watchdog.compiler import evaluate_trace


class Confirmer:
    def __init__(self, control, source, state, rules_for, ingest_wait: float = 15.0, finalize_wait: float = 600.0,
                 clock=time.time):
        self.control = control
        self.source = source
        self.state = state
        self.rules_for = rules_for
        self.ingest_wait = ingest_wait
        self.finalize_wait = finalize_wait
        self.clock = clock

    def start(self, case) -> dict:
        out = self.control.confirm(case.run_id, case.agent)
        for run_id in out["rerun_ids"]:
            self.state.kv_set(f"confirm_session:{run_id}", case.case_id)
        return out

    def check(self, case) -> dict | None:
        job_id = case.extra["confirm_job"]
        status = self.control.confirm_status(job_id)
        if status.get("status") != "done":
            return None
        key = f"confirm_done:{job_id}"
        done_at = self.state.kv_get(key)
        if done_at is None:
            self.state.kv_set(key, str(self.clock()))
            return None
        elapsed = self.clock() - float(done_at)
        if elapsed < self.ingest_wait:
            return None
        if elapsed < self.finalize_wait and not self._finalized(status.get("rerun_ids") or []):
            return None
        return status

    def _finalized(self, rerun_ids: list[str]) -> bool:
        for run_id in rerun_ids:
            traces = self.source.sibling_traces(run_id)
            if not traces:
                return False
            if any(self.source.trace_info(t).finalization != "finalized" for t in traces):
                return False
        return True

    def watchdog_clean(self, rerun_ids: list[str], role: str) -> bool:
        rules = self.rules_for(role)
        for run_id in rerun_ids:
            traces = self.source.sibling_traces(run_id)
            if not traces:
                return False
            for trace_id in traces:
                if evaluate_trace(rules, self.source.spans(trace_id)):
                    return False
        return True
