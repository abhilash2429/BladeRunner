from __future__ import annotations

import json
import secrets
import threading
import uuid
from collections import defaultdict
from datetime import datetime, timezone

import duckdb

SCHEMA = [
    "CREATE SEQUENCE IF NOT EXISTS req_seq",
    """CREATE TABLE IF NOT EXISTS requests (
        id BIGINT DEFAULT nextval('req_seq') PRIMARY KEY,
        ts TIMESTAMPTZ NOT NULL, run_id VARCHAR, agent VARCHAR, step INTEGER,
        tool VARCHAR, service VARCHAR, method VARCHAR, path VARCHAR,
        args VARCHAR, response_meta VARCHAR, planted BOOLEAN DEFAULT FALSE)""",
    """CREATE TABLE IF NOT EXISTS runs (
        run_id VARCHAR PRIMARY KEY, scenario VARCHAR, seed INTEGER, created_at TIMESTAMPTZ,
        agents VARCHAR, canaries VARCHAR, policies VARCHAR, policy_sha VARCHAR, kind VARCHAR)""",
    """CREATE TABLE IF NOT EXISTS notes (
        id VARCHAR PRIMARY KEY, run_id VARCHAR, author VARCHAR, question_id VARCHAR,
        answer VARCHAR, body VARCHAR, ts TIMESTAMPTZ)""",
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ArenaState:
    def __init__(self, db_path: str, read_only: bool = False):
        self.conn = duckdb.connect(db_path, read_only=read_only)
        self._lock = threading.Lock()
        if not read_only:
            for stmt in SCHEMA:
                self.conn.execute(stmt)
        self.flags: dict[tuple[str, str], str] = {}
        self.confirm_jobs: dict[str, dict] = {}
        self._counters: dict[str, int] = defaultdict(int)

    def _exec(self, sql: str, params: tuple = ()):
        with self._lock:
            return self.conn.execute(sql, params).fetchall()

    def _rows(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            cur = self.conn.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def register_run(self, scenario: str, seed: int, agents: list[str], policies: dict[str, str],
                     policy_sha: str, kind: str = "normal", seed_notes: list[dict] | None = None) -> dict:
        run_id = uuid.uuid4().hex[:12]
        canaries = {a: f"BR-CANARY-{secrets.token_hex(4)}" for a in agents}
        self._exec("INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (run_id, scenario, seed, _now(), json.dumps(agents), json.dumps(canaries),
                    json.dumps(policies), policy_sha, kind))
        for agent in agents:
            self.flags[(run_id, agent)] = "run"
        for note in seed_notes or []:
            self.add_note(run_id, note["author"], note.get("question_id"), note.get("answer"), note["body"])
        return {"run_id": run_id, "canaries": canaries}

    def run(self, run_id: str) -> dict | None:
        rows = self._rows("SELECT * FROM runs WHERE run_id = ?", (run_id,))
        if not rows:
            return None
        row = rows[0]
        for key in ("agents", "canaries", "policies"):
            row[key] = json.loads(row[key])
        return row

    def log_request(self, *, run_id, agent, step, tool, service, method, path, args,
                    response_meta=None, planted=False) -> int:
        rows = self._exec(
            "INSERT INTO requests (ts, run_id, agent, step, tool, service, method, path, args, response_meta, planted)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (_now(), run_id, agent, step, tool, service, method, path, json.dumps(args),
             json.dumps(response_meta or {}), planted))
        return rows[0][0]

    def requests_for(self, run_id: str) -> list[dict]:
        rows = self._rows("SELECT * FROM requests WHERE run_id = ? ORDER BY id", (run_id,))
        for row in rows:
            row["args"] = json.loads(row["args"] or "{}")
            row["response_meta"] = json.loads(row["response_meta"] or "{}")
        return rows

    def add_note(self, run_id, author, question_id, answer, body) -> str:
        note_id = uuid.uuid4().hex[:10]
        self._exec("INSERT INTO notes VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (note_id, run_id, author, question_id, None if answer is None else str(answer), body, _now()))
        return note_id

    def notes_for(self, run_id: str) -> list[dict]:
        return self._rows("SELECT id, author, question_id, answer, body FROM notes WHERE run_id = ? ORDER BY ts",
                          (run_id,))

    def set_flag(self, run_id: str, agent: str, flag: str) -> None:
        if (run_id, agent) not in self.flags:
            raise KeyError(f"unknown run/agent {run_id}/{agent}")
        if self.flags[(run_id, agent)] == "stop":
            return
        self.flags[(run_id, agent)] = flag

    def get_flag(self, run_id: str, agent: str) -> str:
        return self.flags.get((run_id, agent), "stop")

    def bump(self, key: str) -> int:
        with self._lock:
            self._counters[key] += 1
            return self._counters[key]
