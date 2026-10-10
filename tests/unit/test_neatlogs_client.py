import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from watchdog.neatlogs_client import (
    NeatlogsCLI,
    NeatlogsClient,
    NeatlogsError,
    derive_key,
    merge_tags,
    retry_delay,
)


def client_with(handler, tmp_path=None):
    sleeps = []
    c = NeatlogsClient("tok", "proj", http=httpx.Client(transport=httpx.MockTransport(handler)),
                       sleep=sleeps.append, ops_log=(tmp_path / "ops.log") if tmp_path else None)
    return c, sleeps


def test_headers_and_backoff_on_429(tmp_path):
    seen = []

    def handler(req):
        seen.append(req)
        if len(seen) == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json={"success": True, "data": {"id": "t1"}})

    c, sleeps = client_with(handler, tmp_path)
    assert c.get_trace("t1") == {"id": "t1"}
    assert sleeps == [2.0]
    assert seen[0].headers["authorization"] == "Bearer tok" and seen[0].headers["x-project-id"] == "proj"
    assert "backoff 429" in (tmp_path / "ops.log").read_text()


def test_error_raises_with_status():
    c, _ = client_with(lambda req: httpx.Response(404, text="nope"))
    with pytest.raises(NeatlogsError) as e:
        c.get_trace("missing")
    assert e.value.status == 404


def test_list_traces_paginates_and_formats_params():
    calls = []

    def handler(req):
        calls.append(dict(req.url.params))
        if "cursor" not in req.url.params:
            return httpx.Response(200, json={"data": {"traces": [{"traceId": "a"}], "page": {"nextCursor": "c2"}}})
        return httpx.Response(200, json={"data": {"traces": [{"traceId": "b"}], "page": {"nextCursor": None}}})

    c, _ = client_with(handler)
    since = datetime(2026, 10, 11, 9, 0, tzinfo=timezone.utc)
    out = c.list_traces(since=since, workflow_name="arena-agent", session_id="r1")
    assert [t["traceId"] for t in out] == ["a", "b"]
    assert calls[0]["from"] == "2026-10-11T09:00:00.000Z"
    assert calls[0]["workflowName"] == "arena-agent" and calls[0]["sessionId"] == "r1"


def test_patch_trace_merges_tags_and_retries_409():
    state = {"rev": 4, "patches": []}

    def handler(req):
        if req.method == "GET":
            return httpx.Response(200, json={"data": {"traceId": "t1", "tags": ["agent:a_1"],
                                                      "metadataRevision": state["rev"]}})
        body = json.loads(req.content)
        state["patches"].append((body, req.headers["idempotency-key"]))
        if len(state["patches"]) == 1:
            state["rev"] = 5
            return httpx.Response(409, json={"title": "conflict"})
        return httpx.Response(200, json={"data": {"metadataRevision": 6}})

    c, sleeps = client_with(handler)
    c.patch_trace("t1", add_tags=["bladerunner", "cat:C1"], trace_name="[BLADERUNNER C1] a_1", idempotency_key="k")
    first, second = state["patches"]
    assert first[0]["expectedRevision"] == 4 and second[0]["expectedRevision"] == 5
    assert second[0]["tags"] == ["agent:a_1", "bladerunner", "cat:C1"]
    assert first[1] != second[1] and len(sleeps) == 1


def test_list_hits_returns_cursor():
    def handler(req):
        return httpx.Response(200, json={"data": {"hits": [{"id": "h1", "traceId": "t", "spanId": "s"}],
                                                  "page": {"hasMore": True, "nextCursor": "n"}}})

    c, _ = client_with(handler)
    hits, cursor = c.list_hits("d1")
    assert hits[0]["id"] == "h1" and cursor == "n"


def test_merge_tags_caps_and_removes():
    existing = ["agent:a_1", "quarantined"]
    out = merge_tags(existing, add=["bladerunner", "fixed"] + [f"x{i}" for i in range(20)], remove=["quarantined"])
    assert out[:3] == ["agent:a_1", "bladerunner", "fixed"] and len(out) == 10 and "quarantined" not in out


def test_derive_key_is_stable_uuid_shaped():
    k = derive_key("case-1", "feedback")
    assert k == derive_key("case-1", "feedback") and k != derive_key("case-1", "tags")
    assert len(k) == 36 and k[14] == "4"


def test_retry_delay_prefers_retry_after_then_reset():
    assert retry_delay({"retry-after": "3"}, 0) == 3.0
    assert retry_delay({"ratelimit-reset": "1000000010"}, 0, now=1000000000.0) == 10.0
    assert retry_delay({"ratelimit-reset": "7"}, 0) == 7.0
    assert 1.0 <= retry_delay({}, 0) <= 1.5


def test_cli_strips_credentials_and_builds_args(monkeypatch):
    monkeypatch.setenv("NEATLOGS_TOKEN", "secret")
    monkeypatch.setenv("NEATLOGS_INGEST_KEY", "secret2")
    monkeypatch.setenv("NEATLOGS_DETECTION_GROUP_ID", "g")
    monkeypatch.setenv("NEATLOGS_HOST", "https://h.example")
    monkeypatch.setenv("NEATLOGS_PROJECT_ID", "proj")
    seen = {}

    def fake_run(cmd, input, text, capture_output, env, timeout):
        seen.update(cmd=cmd, input=input, env=env)

        class R:
            returncode, stdout, stderr = 0, '{"ok": true}', ""

        return R()

    cli = NeatlogsCLI(exe="neatlogs", run=fake_run)
    ok, _ = cli.trace_feedback("t1", {"rating": -100}, "key-1")
    assert ok
    assert seen["cmd"] == ["neatlogs", "feedback", "create", "--trace", "t1", "--body-stdin",
                           "--idempotency-key", "key-1", "--json"]
    assert sorted(k for k in seen["env"] if k.startswith("NEATLOGS_")) == ["NEATLOGS_HOST", "NEATLOGS_PROJECT_ID"]
    assert seen["env"]["NEATLOGS_PROJECT_ID"] == "proj" and json.loads(seen["input"]) == {"rating": -100}


def test_cli_resolves_node_cli_without_venv_scripts(monkeypatch, tmp_path):
    venv = tmp_path / "venv" / "Scripts"
    node = tmp_path / "npm"
    venv.mkdir(parents=True)
    node.mkdir()
    for d, name in ((venv, "neatlogs-sdk"), (node, "neatlogs-node")):
        (d / "neatlogs.cmd").write_text(name)
    monkeypatch.setattr("sys.prefix", str(tmp_path / "venv"))
    monkeypatch.setattr("sys.executable", str(venv / "python.exe"))
    monkeypatch.setenv("PATH", os.pathsep.join([str(venv), str(node)]))
    monkeypatch.setenv("PATHEXT", ".CMD")
    cli = NeatlogsCLI()
    assert Path(cli.exe).parent == node


def test_cli_disabled_skips():
    cli = NeatlogsCLI(exe="neatlogs", run=lambda *a, **k: 1 / 0, enabled=False)
    assert cli.span_feedback("t", "s", {}, "k") == (False, "disabled")


def test_get_retries_trace_processing_409():
    seen = []

    def handler(req):
        seen.append(req)
        if len(seen) == 1:
            return httpx.Response(409, headers={"Retry-After": "1"}, json={"error": {"code": "TRACE_PROCESSING"}})
        return httpx.Response(200, json={"data": {"spans": [{"spanId": "s"}], "page": {"nextCursor": None}}})

    c, sleeps = client_with(handler)
    assert c.get_spans("t1") == [{"spanId": "s"}]
    assert sleeps == [1.0]


def test_other_409_is_not_retried_on_get():
    c, sleeps = client_with(lambda req: httpx.Response(409, json={"title": "conflict"}))
    with pytest.raises(NeatlogsError) as e:
        c.get_trace("t1")
    assert e.value.status == 409 and sleeps == []


def test_get_payload_sends_fields():
    seen = []

    def handler(req):
        seen.append(dict(req.url.params))
        return httpx.Response(200, json={"data": {"fields": {"input": {"value": "x"}}}})

    c, _ = client_with(handler)
    assert c.get_payload("t", "s")["fields"]["input"]["value"] == "x"
    assert seen[0]["fields"] == "input,output"


def test_create_detection_sends_idempotency_key():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(201, json={"data": {"id": "d1"}})

    c, _ = client_with(handler)
    assert c.create_detection({"name": "n"}, "k-1") == {"id": "d1"}
    assert seen[0].headers["idempotency-key"] == "k-1" and seen[0].method == "POST"


def test_patch_trace_sends_uuid_idempotency_keys():
    keys = []

    def handler(req):
        if req.method == "GET":
            return httpx.Response(200, json={"data": {"tags": [], "metadataRevision": 0}})
        keys.append(req.headers["idempotency-key"])
        return httpx.Response(200, json={"data": {}})

    c, _ = client_with(handler)
    c.patch_trace("t1", add_tags=["a"], idempotency_key="case-1")
    assert keys == [derive_key("case-1", "patch0")]


def test_cli_disables_itself_when_node_cli_missing(monkeypatch, caplog):
    monkeypatch.setenv("PATH", "")
    with caplog.at_level("WARNING"):
        cli = NeatlogsCLI(run=lambda *a, **k: 1 / 0)
    assert cli.enabled is False and len([r for r in caplog.records if "neatlogs" in r.getMessage()]) == 1
    assert cli.trace_feedback("t", {}, "k") == (False, "disabled")


def test_cli_explicit_exe_stays_enabled(monkeypatch):
    monkeypatch.setenv("PATH", "")
    assert NeatlogsCLI(exe="neatlogs").enabled is True


def test_cli_env_omits_unset_project_id(monkeypatch):
    monkeypatch.delenv("NEATLOGS_PROJECT_ID", raising=False)
    assert "NEATLOGS_PROJECT_ID" not in NeatlogsCLI(exe="n").env()


def test_cli_timeout_returns_false_timeout():
    def fake_run(cmd, input, text, capture_output, env, timeout):
        raise subprocess.TimeoutExpired(cmd, timeout)

    assert NeatlogsCLI(exe="n", run=fake_run).trace_feedback("t", {}, "k") == (False, "timeout")


def test_cli_failure_prefers_stderr_over_stdout():
    def fake_run(cmd, input, text, capture_output, env, timeout):
        class R:
            returncode, stdout, stderr = 2, "partial out", "real error"

        return R()

    assert NeatlogsCLI(exe="n", run=fake_run).trace_feedback("t", {}, "k") == (False, "real error")


def test_cli_span_feedback_args():
    seen = {}

    def fake_run(cmd, input, text, capture_output, env, timeout):
        seen["cmd"] = cmd

        class R:
            returncode, stdout, stderr = 1, "", "boom"

        return R()

    ok, out = NeatlogsCLI(exe="n", run=fake_run).span_feedback("t", "s", {"rating": 1}, "k")
    assert not ok and out == "boom"
    assert seen["cmd"] == ["n", "feedback", "span-set", "s", "--trace", "t", "--body-stdin",
                           "--idempotency-key", "k", "--json"]


def test_get_raw_spans_paginates_with_raw_limit():
    seen = []

    def handler(req):
        seen.append((req.url.path, dict(req.url.params)))
        if "cursor" not in req.url.params:
            return httpx.Response(200, json={"data": {"spans": [{"spanId": "a"}], "page": {"nextCursor": "c1"}}})
        return httpx.Response(200, json={"data": {"spans": [{"spanId": "b"}], "page": {"nextCursor": None}}})

    c, _ = client_with(handler)
    assert [s["spanId"] for s in c.get_raw_spans("t1")] == ["a", "b"]
    assert seen[0][0].endswith("/traces/t1/raw-spans") and seen[0][1]["limit"] == "25"
    assert seen[1][1]["cursor"] == "c1"


def test_retry_delay_clamps_and_rejects_bad_values():
    assert retry_delay({"retry-after": "100000"}, 0) == 60.0
    assert retry_delay({"ratelimit-reset": "9999999999"}, 0, now=1000000000.0) == 60.0
    for bad in ("inf", "nan", "-5"):
        assert 1.0 <= retry_delay({"retry-after": bad}, 0) <= 1.5
    assert 1.0 <= retry_delay({"ratelimit-reset": "1000000000"}, 0, now=1000000500.0) <= 1.5


@pytest.mark.parametrize("status", [429, 503])
def test_post_retry_reuses_idempotency_key(status):
    seen = []

    def handler(req):
        seen.append(req.headers["idempotency-key"])
        if len(seen) == 1:
            return httpx.Response(status, headers={"Retry-After": "1"})
        return httpx.Response(201, json={"data": {"id": "d1"}})

    c, _ = client_with(handler)
    assert c.create_detection({"name": "n"}, "k-1") == {"id": "d1"}
    assert seen == ["k-1", "k-1"]


@pytest.mark.parametrize("status", [429, 503])
def test_patch_retry_reuses_idempotency_key(status):
    keys = []

    def handler(req):
        if req.method == "GET":
            return httpx.Response(200, json={"data": {"tags": [], "metadataRevision": 0}})
        keys.append(req.headers["idempotency-key"])
        if len(keys) == 1:
            return httpx.Response(status, headers={"Retry-After": "1"})
        return httpx.Response(200, json={"data": {}})

    c, _ = client_with(handler)
    c.patch_trace("t1", add_tags=["a"], idempotency_key="case-1")
    assert len(keys) == 2 and keys[0] == keys[1]


def test_persistent_429_stops_after_max_attempts():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(429, headers={"Retry-After": "1"}, text="slow down")

    c, sleeps = client_with(handler)
    with pytest.raises(NeatlogsError) as e:
        c.get_trace("t1")
    assert e.value.status == 429 and len(calls) == c.max_attempts
    assert len(sleeps) == c.max_attempts - 1


def test_patch_409_exhausts_after_three_attempts():
    patches = []

    def handler(req):
        if req.method == "GET":
            return httpx.Response(200, json={"data": {"tags": [], "metadataRevision": len(patches)}})
        patches.append(req)
        return httpx.Response(409, json={"title": "conflict"})

    c, sleeps = client_with(handler)
    with pytest.raises(NeatlogsError) as e:
        c.patch_trace("t1", add_tags=["a"], idempotency_key="k")
    assert e.value.status == 409 and len(patches) == 3 and len(sleeps) == 2


def test_paged_breaks_on_repeated_cursor():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, json={"data": {"spans": [{"spanId": str(len(calls))}],
                                                  "page": {"nextCursor": "same"}}})

    c, _ = client_with(handler)
    out = c.get_spans("t1")
    assert len(calls) == 2 and len(out) == 2
