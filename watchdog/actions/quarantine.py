from __future__ import annotations

from urllib.parse import quote

import httpx

from common.config import host


class ControlError(RuntimeError):
    pass


class ControlClient:
    def __init__(self, base: str | None = None, http: httpx.Client | None = None):
        self.base = base or f"http://{host('control')}"
        self.http = http or httpx.Client(timeout=10)

    def _send(self, method: str, path: str, body: dict | None = None) -> dict:
        try:
            r = self.http.request(method, self.base + path, json=body)
        except (httpx.HTTPError, httpx.InvalidURL) as e:
            raise ControlError(f"{method} {path}: {type(e).__name__}") from e
        if r.status_code >= 400:
            raise ControlError(f"{method} {path}: {r.status_code}")
        try:
            return r.json()
        except ValueError as e:
            raise ControlError(f"{method} {path}: non-JSON response") from e

    def pause(self, run_id: str, agent: str) -> None:
        self._send("POST", f"/runs/{quote(run_id, safe='')}/pause", {"agent": agent})

    def stop(self, run_id: str, agent: str) -> None:
        self._send("POST", f"/runs/{quote(run_id, safe='')}/stop", {"agent": agent})

    def resume(self, run_id: str, agent: str) -> None:
        self._send("POST", f"/runs/{quote(run_id, safe='')}/resume", {"agent": agent})

    def confirm(self, run_id: str, agent: str) -> dict:
        return self._send("POST", f"/runs/{quote(run_id, safe='')}/confirm", {"agent": agent})

    def confirm_status(self, job_id: str) -> dict:
        return self._send("GET", f"/confirm/{quote(job_id, safe='')}")
