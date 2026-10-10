from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import random
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

from common.config import RUNS_DIR, env

BASE = "https://app.neatlogs.com/api/v1/public"


class NeatlogsError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"neatlogs {status}: {body[:300]}")
        self.status = status
        self.body = body


def derive_key(base: str, purpose: str) -> str:
    digest = hashlib.sha256(f"{base}:{purpose}".encode()).digest()[:16]
    return str(uuid.UUID(bytes=digest, version=4))


def merge_tags(existing, add=(), remove=(), cap: int = 10) -> list[str]:
    out = [t for t in existing if t not in remove]
    for tag in add:
        if tag not in out and tag not in remove:
            out.append(tag)
    return out[:cap]


MAX_HEADER_DELAY = 60.0

log = logging.getLogger(__name__)


def _valid_delay(value: float) -> float | None:
    if math.isfinite(value) and value >= 0:
        return min(value, MAX_HEADER_DELAY)
    return None


def retry_delay(headers, attempt: int, now: float | None = None) -> float:
    now = time.time() if now is None else now
    retry_after = headers.get("retry-after")
    if retry_after:
        try:
            delay = _valid_delay(float(retry_after))
        except ValueError:
            delay = None
        if delay is not None:
            return delay
    reset = headers.get("ratelimit-reset")
    if reset:
        try:
            value = float(reset)
            delay = _valid_delay(value - now if value > 1e9 else value)
        except ValueError:
            delay = None
        if delay is not None:
            return delay
    return min(30.0, 2.0 ** attempt) + random.uniform(0, 0.5)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _data(body):
    return body.get("data", body) if isinstance(body, dict) else body


class NeatlogsClient:
    def __init__(self, token: str, project_id: str, base: str = BASE, http: httpx.Client | None = None,
                 sleep=time.sleep, ops_log: Path | None = None, max_attempts: int = 5):
        self.token = token
        self.project_id = project_id
        self.base = base
        self.http = http or httpx.Client(timeout=30)
        self.sleep = sleep
        self.ops_log = ops_log
        self.max_attempts = max_attempts

    @classmethod
    def from_env(cls, **kwargs) -> "NeatlogsClient":
        return cls(env("NEATLOGS_TOKEN"), env("NEATLOGS_PROJECT_ID"), ops_log=RUNS_DIR / "ops.log", **kwargs)

    def _log(self, message: str) -> None:
        if self.ops_log:
            self.ops_log.parent.mkdir(parents=True, exist_ok=True)
            with self.ops_log.open("a", encoding="utf-8") as f:
                f.write(f"{_iso(datetime.now(timezone.utc))} {message}\n")

    def request(self, method: str, path: str, *, params=None, json_body=None, idempotency_key=None) -> dict:
        headers = {"Authorization": f"Bearer {self.token}", "x-project-id": self.project_id}
        if idempotency_key:
            headers["idempotency-key"] = idempotency_key
        for attempt in range(self.max_attempts):
            r = self.http.request(method, self.base + path, params=params, json=json_body, headers=headers)
            retryable = r.status_code in (429, 503) or (
                method == "GET" and r.status_code == 409 and "TRACE_PROCESSING" in r.text)
            if retryable and attempt < self.max_attempts - 1:
                delay = retry_delay(r.headers, attempt)
                self._log(f"backoff {r.status_code} {method} {path} {delay:.1f}s "
                          f"remaining={r.headers.get('ratelimit-remaining')}")
                self.sleep(delay)
                continue
            if r.status_code >= 400:
                raise NeatlogsError(r.status_code, r.text)
            return r.json() if r.content else {}
        raise NeatlogsError(429, "retries exhausted")

    def _paged(self, path: str, key: str, params: dict) -> list[dict]:
        out: list[dict] = []
        params = dict(params)
        seen: set[str] = set()
        while True:
            data = _data(self.request("GET", path, params=params))
            out.extend(data.get(key, []))
            cursor = (data.get("page") or {}).get("nextCursor")
            if not cursor or cursor in seen:
                return out
            seen.add(cursor)
            params["cursor"] = cursor

    def list_traces(self, *, since: datetime, until: datetime | None = None, workflow_name: str | None = None,
                    session_id: str | None = None, limit: int = 50) -> list[dict]:
        params: dict = {"from": _iso(since), "limit": limit}
        if until:
            params["to"] = _iso(until)
        if workflow_name:
            params["workflowName"] = workflow_name
        if session_id:
            params["sessionId"] = session_id
        return self._paged("/traces", "traces", params)

    def get_trace(self, trace_id: str) -> dict:
        return _data(self.request("GET", f"/traces/{trace_id}"))

    def get_spans(self, trace_id: str) -> list[dict]:
        return self._paged(f"/traces/{trace_id}/spans", "spans", {"limit": 50})

    def get_raw_spans(self, trace_id: str) -> list[dict]:
        return self._paged(f"/traces/{trace_id}/raw-spans", "spans", {"limit": 25})

    def get_payload(self, trace_id: str, span_id: str, fields=("input", "output")) -> dict:
        return _data(self.request("GET", f"/traces/{trace_id}/spans/{span_id}/payload",
                                  params={"fields": ",".join(fields)}))

    def create_detection(self, spec: dict, idempotency_key: str) -> dict:
        return _data(self.request("POST", "/detections", json_body=spec, idempotency_key=idempotency_key))

    def list_hits(self, detection_id: str, cursor: str | None = None, limit: int = 100) -> tuple[list[dict], str | None]:
        params: dict = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        data = _data(self.request("GET", f"/detections/{detection_id}/hits", params=params))
        return data.get("hits", []), (data.get("page") or {}).get("nextCursor")

    def patch_trace(self, trace_id: str, *, add_tags=(), remove_tags=(), trace_name: str | None = None,
                    idempotency_key: str) -> dict:
        for attempt in range(3):
            trace = self.get_trace(trace_id)
            body: dict = {"expectedRevision": trace.get("metadataRevision", 0),
                          "tags": merge_tags(trace.get("tags") or [], add_tags, remove_tags)}
            if trace_name:
                body["traceName"] = trace_name[:255]
            try:
                return _data(self.request("PATCH", f"/traces/{trace_id}", json_body=body,
                                          idempotency_key=derive_key(idempotency_key, f"patch{attempt}")))
            except NeatlogsError as e:
                if e.status != 409 or attempt == 2:
                    raise
                self.sleep(1.0 + attempt)
        raise NeatlogsError(409, "unreachable")


def _venv_dirs() -> set[str]:
    dirs = {os.path.dirname(sys.executable)}
    roots = [sys.prefix, os.environ.get("VIRTUAL_ENV")]
    for root in filter(None, roots):
        dirs.update(os.path.join(root, sub) for sub in ("Scripts", "bin"))
    return {os.path.normcase(os.path.normpath(d)) for d in dirs}


def _node_cli_path() -> str:
    venv = _venv_dirs()
    keep = [p for p in os.environ.get("PATH", "").split(os.pathsep)
            if p and os.path.normcase(os.path.normpath(p)) not in venv]
    return os.pathsep.join(keep)


class NeatlogsCLI:
    def __init__(self, exe: str | None = None, run=subprocess.run, enabled: bool = True):
        self.exe = exe or shutil.which("neatlogs", path=_node_cli_path())
        self.run = run
        self.enabled = enabled and self.exe is not None
        if enabled and self.exe is None:
            log.warning("neatlogs Node CLI not found outside the venv; CLI feedback disabled")

    def env(self) -> dict:
        keep = ("NEATLOGS_HOST", "NEATLOGS_PROJECT_ID")
        e = {k: v for k, v in os.environ.items() if not k.startswith("NEATLOGS_") or k in keep}
        e["NEATLOGS_HOST"] = os.environ.get("NEATLOGS_HOST", "https://app.neatlogs.com")
        if "NEATLOGS_PROJECT_ID" not in os.environ:
            e.pop("NEATLOGS_PROJECT_ID", None)
        return e

    def _call(self, args: list[str], body: dict) -> tuple[bool, str]:
        if not self.enabled:
            return False, "disabled"
        try:
            r = self.run([self.exe, *args, "--json"], input=json.dumps(body), text=True, capture_output=True,
                         env=self.env(), timeout=60)
        except subprocess.TimeoutExpired:
            return False, "timeout"
        if r.returncode == 0:
            return True, (r.stdout or r.stderr or "")[-500:]
        return False, (r.stderr or r.stdout or "")[-500:]

    def trace_feedback(self, trace_id: str, body: dict, idempotency_key: str) -> tuple[bool, str]:
        return self._call(["feedback", "create", "--trace", trace_id, "--body-stdin",
                           "--idempotency-key", idempotency_key], body)

    def span_feedback(self, trace_id: str, span_id: str, body: dict, idempotency_key: str) -> tuple[bool, str]:
        return self._call(["feedback", "span-set", span_id, "--trace", trace_id, "--body-stdin",
                           "--idempotency-key", idempotency_key], body)
