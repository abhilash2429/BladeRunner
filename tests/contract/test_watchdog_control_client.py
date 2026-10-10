import json

import httpx

from watchdog.actions.quarantine import ControlClient

WATCHDOG_ROUTES = ("/pause", "/stop", "/resume", "/confirm")


def test_control_client_only_touches_watchdog_routes():
    paths = []

    def handler(req):
        paths.append((req.method, req.url.path))
        if req.url.path.startswith("/confirm/"):
            return httpx.Response(200, json={"status": "done", "rerun_clean": True, "control_clean": True,
                                             "rerun_ids": ["a"]})
        if req.url.path.endswith("/confirm"):
            return httpx.Response(200, json={"job_id": "j1", "rerun_ids": ["a"]})
        return httpx.Response(200, json={"ok": True})

    c = ControlClient(base="http://127.0.0.1:8100", http=httpx.Client(transport=httpx.MockTransport(handler)))
    c.pause("r1", "a_1")
    c.stop("r1", "a_1")
    c.resume("r1", "a_1")
    assert c.confirm("r1", "a_1")["job_id"] == "j1"
    assert c.confirm_status("j1")["status"] == "done"
    for method, path in paths:
        assert path.startswith("/confirm/") or path.endswith(WATCHDOG_ROUTES), path
    assert not any("agent-config" in p or p == "/runs" for _, p in paths)
