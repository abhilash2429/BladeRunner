import json

import httpx

from watchdog.actions.quarantine import ControlClient


def test_control_client_only_touches_watchdog_routes():
    seen = []

    def handler(req):
        body = json.loads(req.content) if req.content else None
        seen.append((req.method, req.url.path, body))
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
    body = {"agent": "a_1"}
    assert seen == [
        ("POST", "/runs/r1/pause", body),
        ("POST", "/runs/r1/stop", body),
        ("POST", "/runs/r1/resume", body),
        ("POST", "/runs/r1/confirm", body),
        ("GET", "/confirm/j1", None),
    ]


def test_control_client_quotes_hostile_ids():
    raw = []

    def handler(req):
        raw.append((req.method, req.url.raw_path.decode()))
        return httpx.Response(200, json={"ok": True})

    c = ControlClient(base="http://127.0.0.1:8100", http=httpx.Client(transport=httpx.MockTransport(handler)))
    c.pause("r/../x?y", "a_1")
    c.confirm_status("j/../../z#f")
    assert raw == [("POST", "/runs/r%2F..%2Fx%3Fy/pause"), ("GET", "/confirm/j%2F..%2F..%2Fz%23f")]
