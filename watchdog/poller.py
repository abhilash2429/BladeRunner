from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from common.policy import role_of
from watchdog.compiler import Rule, evaluate_trace

PENDING = "pending:"
FAILED = ("dlq", "failed")


@dataclass
class Flag:
    trace_id: str
    run_id: str | None
    agent: str | None
    source: str
    hits: list[dict]
    marks: list[tuple[str, str]] = field(default_factory=list)


class Poller:
    def __init__(self, source, state, client=None, window: timedelta = timedelta(minutes=30),
                 now=lambda: datetime.now(timezone.utc), pending_max_age: timedelta = timedelta(hours=6)):
        self.source = source
        self.state = state
        self.client = client
        self.window = window
        self.now = now
        self.pending_max_age = pending_max_age
        self.rules_by_role: dict[str, list[Rule]] = {}
        self.detections: dict[str, str] = {}
        self._errored = False

    def _skip(self, info) -> bool:
        return bool(info.session_id and self.state.kv_get(f"confirm_session:{info.session_id}"))

    def _stale(self, info) -> bool:
        # Detections return every hit they ever produced; the sweep only looks at the window.
        try:
            started = datetime.fromisoformat(info.started_at.replace("Z", "+00:00"))
        except ValueError:
            return False
        return started.tzinfo is not None and started < self.now() - self.window

    def _record_error(self, e: Exception, detail: str = "") -> None:
        self._errored = True
        status = getattr(e, "status", None)
        text = type(e).__name__ + (f" {status}" if status else "") + (f": {detail}" if detail else "")
        self.state.kv_set("last_poll_error", text[:300])

    def _poll_detection(self, detection_id: str, rule_id: str, flags: list[Flag]) -> None:
        cursor = None
        visited: set[str] = set()
        taken: set[str] = set()
        while True:
            hits, next_cursor = self.client.list_hits(detection_id, cursor)
            for h in hits:
                key = f"hit:{h['id']}"
                if h["id"] in taken or self.state.kv_get(key):
                    continue
                try:
                    info = self.source.trace_info(h["traceId"])
                except Exception as e:
                    self._record_error(e)
                    continue
                taken.add(h["id"])
                if not info.agent or self._skip(info) or self._stale(info):
                    self.state.kv_set(key, "1")
                    continue
                flags.append(Flag(info.trace_id, info.session_id, info.agent, "native",
                                  [{"rule_id": rule_id, "span_id": h["spanId"]}], [(key, "1")]))
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

    def _track_pending(self, trace_id: str) -> None:
        key = PENDING + trace_id
        first = self.state.kv_get(key)
        try:
            since = datetime.fromisoformat(first) if first else None
        except ValueError:
            since = None
        if since is None or since.tzinfo is None:
            self.state.kv_set(key, self.now().isoformat())
        elif self.now() - since > self.pending_max_age:
            self.state.kv_delete(key)
            self._record_error(TimeoutError(), f"trace {trace_id} still not finalized, dropped")

    def _consider(self, info, flags: list[Flag]) -> None:
        if not info.agent or self._skip(info):
            self.state.kv_delete(PENDING + info.trace_id)
            return
        if info.finalization in FAILED:
            self.state.kv_delete(PENDING + info.trace_id)
            self._record_error(RuntimeError(), f"trace {info.trace_id} {info.finalization} in neatlogs, skipped")
            return
        if info.finalization != "finalized":
            self._track_pending(info.trace_id)
            return
        key, marker = f"seen:{info.trace_id}", f"{info.spans_count}:{info.finalization}"
        if self.state.kv_get(key) == marker:
            self.state.kv_delete(PENDING + info.trace_id)
            return
        rules = self.rules_by_role.get(role_of(info.agent))
        if not rules:
            return
        try:
            spans = self.source.spans(info.trace_id)
        except Exception as e:
            self._record_error(e)
            return
        try:
            hits = evaluate_trace(rules, spans)
        except Exception as e:
            self._record_error(e)
            hits = []
        if hits:
            flags.append(Flag(info.trace_id, info.session_id, info.agent, "local",
                              [{"rule_id": h.rule_id, "span_id": h.span_id} for h in hits], [(key, marker)]))
            return
        self.state.kv_set(key, marker)
        self.state.kv_delete(PENDING + info.trace_id)

    def sweep(self) -> list[Flag]:
        now = self.now()
        flags: list[Flag] = []
        listed: set[str] = set()
        for info in self.source.list_traces(now - self.window, now):
            listed.add(info.trace_id)
            self._consider(info, flags)
        for key in self.state.kv_keys(PENDING):
            trace_id = key[len(PENDING):]
            if trace_id in listed:
                continue
            try:
                info = self.source.trace_info(trace_id)
            except Exception as e:
                self._record_error(e)
                continue
            self._consider(info, flags)
        return flags

    def tick(self) -> list[str]:
        self._errored = False
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
            try:
                case, is_changed = self.state.open_case(flag.trace_id, flag.run_id, flag.agent, flag.source,
                                                        flag.hits)
            except Exception as e:
                self._record_error(e)
                continue
            for key, value in flag.marks:
                self.state.kv_set(key, value)
            if flag.source == "local":
                self.state.kv_delete(PENDING + flag.trace_id)
            if is_changed and case.case_id not in changed:
                changed.append(case.case_id)
        if not self._errored and self.state.kv_get("last_poll_error"):
            self.state.kv_set("last_poll_error", "")
        return changed
