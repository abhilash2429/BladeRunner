from __future__ import annotations

import json
import time

from watchdog.compiler import evaluate_trace


class Confirmer:
    def __init__(self, control, source, state, rules_for, ingest_wait: float = 15.0, finalize_wait: float = 600.0,
                 deadline: float = 2700.0, clock=time.time):
        self.control = control
        self.source = source
        self.state = state
        self.rules_for = rules_for
        self.ingest_wait = ingest_wait
        self.finalize_wait = finalize_wait
        self.deadline = deadline
        self.clock = clock

    def start(self, case) -> dict:
        out = self.control.confirm(case.run_id, case.agent)
        for run_id in out["rerun_ids"]:
            self.state.kv_set(f"confirm_session:{run_id}", case.case_id)
        self.state.kv_set(f"confirm_started:{out['job_id']}", str(self.clock()))
        return out

    def check(self, case) -> dict | None:
        job_id = case.extra["confirm_job"]
        started = self.state.kv_get(f"confirm_started:{job_id}")
        if started is None:
            self.state.kv_set(f"confirm_started:{job_id}", str(self.clock()))
        elif self.clock() - float(started) > self.deadline:
            return {"status": "timeout"}
        status = self.control.confirm_status(job_id)
        if status.get("status") == "error":
            return {"status": "error"}
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
        if elapsed < self.finalize_wait and not self._settled(job_id, status.get("rerun_ids") or []):
            return None
        return status

    def _settled(self, job_id: str, rerun_ids: list[str]) -> bool:
        siblings = {run_id: sorted(self.source.sibling_traces(run_id)) for run_id in rerun_ids}
        if any(not traces for traces in siblings.values()):
            return False
        if any(self.source.trace_info(t).finalization != "finalized" for ts in siblings.values() for t in ts):
            return False
        key, snapshot = f"confirm_siblings:{job_id}", json.dumps(siblings, sort_keys=True)
        previous = self.state.kv_get(key)
        self.state.kv_set(key, snapshot)
        return previous == snapshot

    def watchdog_clean(self, rerun_ids: list[str], role: str) -> bool:
        rules = self.rules_for(role)
        if not rules:
            return False
        for run_id in rerun_ids:
            traces = self.source.sibling_traces(run_id)
            if not traces:
                return False
            for trace_id in traces:
                if self.source.trace_info(trace_id).finalization != "finalized":
                    return False
                spans = self.source.spans(trace_id)
                if not spans or evaluate_trace(rules, spans):
                    return False
        return True
