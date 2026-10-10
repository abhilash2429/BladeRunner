from __future__ import annotations

import json
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone

import duckdb

TRANSITIONS: dict[str, set[str]] = {
    "flagged": {"paused"},
    "paused": {"investigating"},
    "investigating": {"investigated", "needs_review"},
    "investigated": {"verified", "needs_review", "dismissed"},
    "dismissed": {"flagged"},
    "verified": {"quarantined", "quarantine_failed"},
    "quarantine_failed": {"quarantined"},
    "quarantined": {"patching"},
    "patching": {"patched", "patch_failed"},
    "patch_failed": {"patching"},
    "patched": {"confirming"},
    "confirming": {"fixed", "not_fixed"},
}
ACTIVE = ("flagged", "paused", "investigating", "investigated", "verified", "quarantine_failed", "quarantined",
          "patching", "patch_failed", "patched", "confirming")
_STAMPS = {"paused": "paused_at", "quarantined": "quarantined_at"}

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS cases (
        case_id VARCHAR PRIMARY KEY, trace_id VARCHAR UNIQUE, run_id VARCHAR, agent VARCHAR,
        status VARCHAR, source VARCHAR, evidence VARCHAR, findings VARCHAR, idem_key VARCHAR,
        flagged_at TIMESTAMPTZ, paused_at TIMESTAMPTZ, quarantined_at TIMESTAMPTZ, extra VARCHAR,
        updated_at TIMESTAMPTZ)""",
    "CREATE SEQUENCE IF NOT EXISTS event_seq",
    """CREATE TABLE IF NOT EXISTS case_events (
        id BIGINT DEFAULT nextval('event_seq') PRIMARY KEY, case_id VARCHAR, ts TIMESTAMPTZ,
        kind VARCHAR, data VARCHAR)""",
    "CREATE TABLE IF NOT EXISTS kv (key VARCHAR PRIMARY KEY, value VARCHAR)",
]


class InvalidTransition(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fresh(hits: list[dict], seen: set[tuple[str, str]]) -> list[dict]:
    out = []
    for h in hits:
        key = (h["rule_id"], h["span_id"])
        if key not in seen:
            seen.add(key)
            out.append(h)
    return out


@dataclass
class Case:
    case_id: str
    trace_id: str
    run_id: str | None
    agent: str | None
    status: str
    source: str
    evidence: list[dict]
    findings: list[dict]
    idem_key: str
    flagged_at: datetime | None
    paused_at: datetime | None
    quarantined_at: datetime | None
    extra: dict = field(default_factory=dict)


class WatchdogState:
    def __init__(self, db_path: str):
        self.conn = duckdb.connect(db_path)
        self._lock = threading.RLock()
        self._in_tx = False
        for stmt in SCHEMA:
            self.conn.execute(stmt)

    def _rows(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            cur = self.conn.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def _exec(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            self.conn.execute(sql, params)

    @contextmanager
    def _tx(self):
        with self._lock:
            if self._in_tx:
                yield
                return
            self.conn.execute("BEGIN TRANSACTION")
            self._in_tx = True
            try:
                yield
                self.conn.execute("COMMIT")
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            finally:
                self._in_tx = False

    @staticmethod
    def _case(row: dict) -> Case:
        return Case(row["case_id"], row["trace_id"], row["run_id"], row["agent"], row["status"], row["source"],
                    json.loads(row["evidence"]), json.loads(row["findings"]), row["idem_key"],
                    row["flagged_at"], row["paused_at"], row["quarantined_at"], json.loads(row["extra"]))

    def get(self, case_id: str) -> Case:
        rows = self._rows("SELECT * FROM cases WHERE case_id = ?", (case_id,))
        if not rows:
            raise KeyError(case_id)
        return self._case(rows[0])

    def case_for_trace(self, trace_id: str) -> Case | None:
        rows = self._rows("SELECT * FROM cases WHERE trace_id = ?", (trace_id,))
        return self._case(rows[0]) if rows else None

    def log_event(self, case_id: str, kind: str, data=None) -> None:
        self._exec("INSERT INTO case_events (case_id, ts, kind, data) VALUES (?, ?, ?, ?)",
                   (case_id, _now(), kind, json.dumps(data or {}, default=str)))

    def events(self, case_id: str) -> list[dict]:
        rows = self._rows("SELECT ts, kind, data FROM case_events WHERE case_id = ? ORDER BY id", (case_id,))
        for r in rows:
            r["data"] = json.loads(r["data"])
        return rows

    def open_case(self, trace_id, run_id, agent, source, hits: list[dict]) -> tuple[Case, bool]:
        with self._tx():
            existing = self.case_for_trace(trace_id)
            if existing is None:
                hits = _fresh(hits, set())
                case_id = uuid.uuid4().hex
                self._exec("INSERT INTO cases VALUES (?, ?, ?, ?, 'flagged', ?, ?, '[]', ?, ?, NULL, NULL, '{}', ?)",
                           (case_id, trace_id, run_id, agent, source, json.dumps(hits), str(uuid.uuid4()),
                            _now(), _now()))
                self.log_event(case_id, "opened", {"source": source, "hits": hits})
                return self.get(case_id), True
            new = _fresh(hits, {(h["rule_id"], h["span_id"]) for h in existing.evidence})
            new_source = existing.source if existing.source == source else "both"
            self._exec("UPDATE cases SET evidence = ?, source = ?, updated_at = ? WHERE case_id = ?",
                       (json.dumps(existing.evidence + new), new_source, _now(), existing.case_id))
            if new:
                self.log_event(existing.case_id, "evidence", {"hits": new})
                if existing.status == "dismissed":
                    self.transition(existing.case_id, "flagged")
            return self.get(existing.case_id), bool(new)

    def transition(self, case_id: str, to: str) -> Case:
        with self._tx():
            case = self.get(case_id)
            if to not in TRANSITIONS.get(case.status, set()):
                raise InvalidTransition(f"{case.status} -> {to}")
            stamp = _STAMPS.get(to)
            if stamp:
                self._exec(f"UPDATE cases SET status = ?, {stamp} = ?, updated_at = ? WHERE case_id = ?",
                           (to, _now(), _now(), case_id))
            else:
                self._exec("UPDATE cases SET status = ?, updated_at = ? WHERE case_id = ?", (to, _now(), case_id))
            self.log_event(case_id, "transition", {"from": case.status, "to": to})
            return self.get(case_id)

    def set_findings(self, case_id: str, findings: list[dict]) -> None:
        with self._tx():
            self.get(case_id)
            self._exec("UPDATE cases SET findings = ?, updated_at = ? WHERE case_id = ?",
                       (json.dumps(findings), _now(), case_id))
            self.log_event(case_id, "findings", {"findings": findings})

    def bump_retry(self, case_id: str, kind: str) -> int:
        with self._tx():
            extra = self.get(case_id).extra
            retries = extra.setdefault("retries", {})
            retries[kind] = retries.get(kind, 0) + 1
            self._exec("UPDATE cases SET extra = ?, updated_at = ? WHERE case_id = ?",
                       (json.dumps(extra, default=str), _now(), case_id))
            return retries[kind]

    def set_extra(self, case_id: str, **kv) -> None:
        if "retries" in kv:
            raise ValueError("retries is reserved for bump_retry")
        with self._tx():
            extra = self.get(case_id).extra
            extra.update(kv)
            self._exec("UPDATE cases SET extra = ?, updated_at = ? WHERE case_id = ?",
                       (json.dumps(extra, default=str), _now(), case_id))

    def active_cases(self, status: str | None = None) -> list[Case]:
        statuses = (status,) if status else ACTIVE
        marks = ", ".join("?" for _ in statuses)
        rows = self._rows(f"SELECT * FROM cases WHERE status IN ({marks}) ORDER BY flagged_at", tuple(statuses))
        return [self._case(r) for r in rows]

    def all_cases(self) -> list[Case]:
        return [self._case(r) for r in self._rows("SELECT * FROM cases ORDER BY flagged_at")]

    def kv_get(self, key: str) -> str | None:
        rows = self._rows("SELECT value FROM kv WHERE key = ?", (key,))
        return rows[0]["value"] if rows else None

    def kv_set(self, key: str, value: str) -> None:
        self._exec("INSERT OR REPLACE INTO kv VALUES (?, ?)", (key, value))

    def kv_delete(self, key: str) -> None:
        self._exec("DELETE FROM kv WHERE key = ?", (key,))

    def kv_keys(self, prefix: str) -> list[str]:
        return [r["key"] for r in self._rows("SELECT key FROM kv WHERE starts_with(key, ?) ORDER BY key", (prefix,))]
