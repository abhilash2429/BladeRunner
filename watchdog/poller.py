from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from common.policy import role_of
from watchdog.compiler import Rule, evaluate_trace


@dataclass
class Flag:
    trace_id: str
    run_id: str | None
    agent: str | None
    source: str
    hits: list[dict]


class Poller:
    def __init__(self, source, state, client=None, window: timedelta = timedelta(minutes=30),
                 now=lambda: datetime.now(timezone.utc)):
        self.source = source
        self.state = state
        self.client = client
        self.window = window
        self.now = now
        self.rules_by_role: dict[str, list[Rule]] = {}
        self.detections: dict[str, str] = {}

    def _skip(self, info) -> bool:
        return bool(info.session_id and self.state.kv_get(f"confirm_session:{info.session_id}"))

    def _record_error(self, e: Exception) -> None:
        self.state.kv_set("last_poll_error", f"{type(e).__name__}: {e}"[:300])

    def _poll_detection(self, detection_id: str, rule_id: str, flags: list[Flag]) -> None:
        cursor = None
        visited: set[str] = set()
        while True:
            hits, next_cursor = self.client.list_hits(detection_id, cursor)
            for h in hits:
                if self.state.kv_get(f"hit:{h['id']}"):
                    continue
                try:
                    info = self.source.trace_info(h["traceId"])
                except Exception as e:
                    self._record_error(e)
                    continue
                self.state.kv_set(f"hit:{h['id']}", "1")
                if not info.agent or self._skip(info):
                    continue
                flags.append(Flag(info.trace_id, info.session_id, info.agent, "native",
                                  [{"rule_id": rule_id, "span_id": h["spanId"]}]))
            if not next_cursor or next_cursor in visited:
                return
            visited.add(next_cursor)
            cursor = next_cursor

    def poll_native(self) -> list[Flag]:
        if self.client is None:
            return []
        flags: list[Flag] = []
        for detection_id, rule_id in self.detections.items():
            try:
                self._poll_detection(detection_id, rule_id, flags)
            except Exception as e:
                self._record_error(e)
        return flags

    def sweep(self) -> list[Flag]:
        now = self.now()
        flags: list[Flag] = []
        for info in self.source.list_traces(now - self.window, now):
            if info.finalization != "finalized" or not info.agent or self._skip(info):
                continue
            marker = f"{info.spans_count}:{info.finalization}"
            key = f"seen:{info.trace_id}"
            if self.state.kv_get(key) == marker:
                continue
            try:
                hits = evaluate_trace(self.rules_by_role.get(role_of(info.agent), []),
                                      self.source.spans(info.trace_id))
            except Exception as e:
                self._record_error(e)
                continue
            self.state.kv_set(key, marker)
            if hits:
                flags.append(Flag(info.trace_id, info.session_id, info.agent, "local",
                                  [{"rule_id": h.rule_id, "span_id": h.span_id} for h in hits]))
        return flags

    def tick(self) -> list[str]:
        changed: list[str] = []
        try:
            native = self.poll_native()
        except Exception as e:
            self._record_error(e)
            native = []
        try:
            local = self.sweep()
        except Exception as e:
            self._record_error(e)
            local = []
        for flag in native + local:
            case, is_changed = self.state.open_case(flag.trace_id, flag.run_id, flag.agent, flag.source, flag.hits)
            if is_changed and case.case_id not in changed:
                changed.append(case.case_id)
        return changed
