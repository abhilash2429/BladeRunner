from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from watchdog.neatlogs_client import NeatlogsError

log = logging.getLogger(__name__)

_WRAPPER_KEYS = ("truncated", "mimeType", "available")


@dataclass(frozen=True)
class Span:
    span_id: str
    parent_id: str | None
    name: str
    kind: str
    start: str
    input: str
    output: str


@dataclass(frozen=True)
class TraceInfo:
    trace_id: str
    session_id: str | None
    agent: str | None
    tags: tuple[str, ...]
    spans_count: int
    finalization: str
    started_at: str


def _first(d: dict, *keys, default=None):
    for key in keys:
        if d.get(key) is not None:
            return d[key]
    return default


def _is_io_wrapper(value: dict) -> bool:
    return any(k in value for k in _WRAPPER_KEYS) and ("preview" in value or "value" in value)


def text_of(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and _is_io_wrapper(value):
        inner = _first(value, "preview", "value")
        return "" if inner is None else inner if isinstance(inner, str) else json.dumps(inner)
    return json.dumps(value)


def _is_truncated(value) -> bool:
    return isinstance(value, dict) and bool(value.get("truncated"))


def normalize_span(raw: dict) -> Span:
    parent = _first(raw, "parentSpanId", "parentId")
    return Span(
        span_id=str(_first(raw, "spanId", "id", default="")),
        parent_id=str(parent) if parent is not None else None,
        name=str(_first(raw, "spanName", "name", default="")),
        kind=str(_first(raw, "spanType", "kind", "spanKind", "type", default="")).upper(),
        start=str(_first(raw, "startedAt", "startTime", "start", default="")),
        input=text_of(_first(raw, "input", "inputValue")),
        output=text_of(_first(raw, "output", "outputValue")),
    )


def _agent_from_tags(tags) -> str | None:
    for tag in tags:
        if isinstance(tag, str) and tag.startswith("agent:"):
            return tag.split(":", 1)[1]
    return None


def trace_info_from(raw: dict) -> TraceInfo:
    tags = tuple(raw.get("tags") or ())
    return TraceInfo(
        trace_id=str(_first(raw, "traceId", "id", default="")),
        session_id=_first(raw, "sessionId"),
        agent=_agent_from_tags(tags),
        tags=tags,
        spans_count=int(_first(raw, "spansCount", "spanCount", default=0) or 0),
        finalization=str(_first(raw, "finalizationStatus", default="unknown")),
        started_at=str(_first(raw, "startedAt", "startTime", default="")),
    )


def _find_target(obj):
    if isinstance(obj, dict):
        if "target_host" in obj:
            return obj
        for value in obj.values():
            found = _find_target(value)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _find_target(value)
            if found is not None:
                return found
    return None


def tool_call(span: Span) -> dict | None:
    try:
        parsed = json.loads(span.input)
        if isinstance(parsed, str):
            parsed = json.loads(parsed)
    except (json.JSONDecodeError, TypeError):
        return None
    found = _find_target(parsed)
    if found is None:
        return None
    return {"tool": found.get("tool", span.name), "target_host": str(found["target_host"]),
            "args": found.get("args") or {}}


def compact_tree(spans: list[Span], limit: int = 500) -> list[dict]:
    out = []
    for s in spans:
        if s.kind == "LLM":
            inp, outp = "<llm prompt omitted>", s.output[-limit:]
        else:
            inp, outp = s.input[:limit], s.output[:limit]
        out.append({"id": s.span_id, "parent": s.parent_id, "name": s.name, "kind": s.kind,
                    "start": s.start, "input": inp, "output": outp})
    return out


_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def _when(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return _EPOCH
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _order(span: Span) -> tuple[datetime, int, str]:
    return _when(span.start), 0 if span.parent_id is None else 1, span.span_id


def _payload_fields(payload: dict) -> dict:
    fields = payload.get("fields")
    source = fields if isinstance(fields, dict) else payload
    return {"input": text_of(source.get("input")), "output": text_of(source.get("output"))}


class LiveSource:
    def __init__(self, client, workflow_name: str = "arena-agent", now=lambda: datetime.now(timezone.utc),
                 clock=time.monotonic, cache_ttl: float = 300):
        self.client = client
        self.workflow_name = workflow_name
        self.now = now
        self.clock = clock
        self.cache_ttl = cache_ttl
        self._cache: dict[tuple, tuple[float, object]] = {}
        self._lock = threading.Lock()

    def _cached(self, key: tuple):
        with self._lock:
            hit = self._cache.get(key)
            return hit[1] if hit and self.clock() - hit[0] <= self.cache_ttl else None

    def _store(self, key: tuple, value) -> None:
        now = self.clock()
        with self._lock:
            self._cache = {k: v for k, v in self._cache.items() if now - v[0] <= self.cache_ttl}
            self._cache[key] = (now, value)

    def list_traces(self, since: datetime, until: datetime | None) -> list[TraceInfo]:
        return [trace_info_from(t) for t in self.client.list_traces(since=since, until=until,
                                                                    workflow_name=self.workflow_name)]

    def trace_info(self, trace_id: str) -> TraceInfo:
        return trace_info_from(self.client.get_trace(trace_id))

    def spans(self, trace_id: str) -> list[Span]:
        cached = self._cached(("spans", trace_id))
        if cached is not None:
            return list(cached)
        spans = []
        degraded = []
        for raw in self.client.get_raw_spans(trace_id):
            span = normalize_span(raw)
            if _is_truncated(raw.get("input")) or _is_truncated(raw.get("output")):
                try:
                    full = self.payload(trace_id, span.span_id)
                except NeatlogsError as e:
                    degraded.append((span.span_id, e.status))
                    spans.append(span)
                    continue
                span = Span(span.span_id, span.parent_id, span.name, span.kind, span.start,
                            full["input"] if _is_truncated(raw.get("input")) else span.input,
                            full["output"] if _is_truncated(raw.get("output")) else span.output)
            spans.append(span)
        spans.sort(key=_order)
        if degraded:
            log.warning("trace %s: payload fetch failed for %d truncated span(s), using previews: %s",
                        trace_id, len(degraded), degraded)
        else:
            self._store(("spans", trace_id), tuple(spans))
        return spans

    def payload(self, trace_id: str, span_id: str) -> dict:
        cached = self._cached(("payload", trace_id, span_id))
        if cached is None:
            cached = _payload_fields(self.client.get_payload(trace_id, span_id))
            self._store(("payload", trace_id, span_id), cached)
        return dict(cached)

    def sibling_traces(self, session_id: str) -> list[str]:
        since = self.now() - timedelta(hours=12)
        rows = self.client.list_traces(since=since, session_id=session_id, workflow_name=self.workflow_name)
        return [trace_info_from(t).trace_id for t in rows]


class FrozenSource:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.manifest = json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))

    def _info(self, trace_id: str, entry: dict) -> TraceInfo:
        return TraceInfo(trace_id, entry.get("session_id"), entry.get("agent"), tuple(entry.get("tags") or ()),
                         0, "finalized", entry.get("started_at", ""))

    def list_traces(self, since, until) -> list[TraceInfo]:
        return [self._info(tid, e) for tid, e in self.manifest["traces"].items()]

    def trace_info(self, trace_id: str) -> TraceInfo:
        return self._info(trace_id, self.manifest["traces"][trace_id])

    def spans(self, trace_id: str) -> list[Span]:
        rows = json.loads((self.root / "spans" / f"{trace_id}.json").read_text(encoding="utf-8"))
        return sorted((Span(**r) for r in rows), key=_order)

    def payload(self, trace_id: str, span_id: str) -> dict:
        for s in self.spans(trace_id):
            if s.span_id == span_id:
                return {"input": s.input, "output": s.output}
        return {"input": "", "output": ""}

    def sibling_traces(self, session_id: str) -> list[str]:
        return [tid for tid, e in self.manifest["traces"].items() if e.get("session_id") == session_id]
