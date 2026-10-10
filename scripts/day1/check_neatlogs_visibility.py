import asyncio
import inspect
import json
import threading
import time
from datetime import datetime, timedelta, timezone

from common.config import env, load_env

load_env()
import neatlogs  # noqa: E402

import _rest  # noqa: E402
from check_mcp import mcp_check  # noqa: E402

for name in ("init", "span", "trace", "identify", "wrap"):
    fn = getattr(neatlogs, name, None)
    print(name, inspect.signature(fn) if fn else "MISSING")

neatlogs.init(api_key=env("NEATLOGS_INGEST_KEY"), workflow_name="day1-visibility",
              tags=["agent:day1_agent"], flush_interval=1.0)

session = f"day1-{int(time.time())}"
start = datetime.now(timezone.utc) - timedelta(seconds=10)
release = threading.Event()


@neatlogs.span(kind="WORKFLOW", name="day1-run", session_id=session)
def run():
    @neatlogs.span(kind="TOOL", name="day1_notes_write", tool_name="day1_notes_write")
    def tool_call(tool, target_host, args):
        try:
            from opentelemetry import trace
            trace.get_current_span().set_attribute("neatlogs.tool.target_host", target_host)
        except ImportError:
            print("opentelemetry not importable")
        return "ok"

    tool_call(tool="day1_notes_write", target_host="127.0.0.1:8102", args={"body": "hello"})
    neatlogs.flush()
    release.wait(90)


worker = threading.Thread(target=run)
worker.start()
t0 = time.time()
trace_id = first_trace = first_tool = None
with _rest.client() as c:
    while time.time() - t0 < 75:
        r = c.get("/traces", params={"from": start.isoformat().replace("+00:00", "Z"),
                                     "workflowName": "day1-visibility", "limit": 50})
        body = r.json()
        if first_trace is None:
            _rest.save("traces_list", body)
        items = (body.get("data") or {}).get("traces") or (body.get("data") or {}).get("items") or []
        for t in items:
            if t.get("sessionId") == session or trace_id is None:
                trace_id = t.get("traceId") or t.get("id")
        if trace_id and first_trace is None:
            first_trace = time.time() - t0
        if trace_id:
            sp = c.get(f"/traces/{trace_id}/spans")
            raw_open = c.get(f"/traces/{trace_id}/raw-spans")
            print(f"  open workflow: spans={sp.status_code} raw-spans={raw_open.status_code}")
            if first_tool is None and (sp.status_code == 200 or raw_open.status_code == 200):
                first_tool = time.time() - t0
                _rest.save("spans_open_workflow", sp.json())
                _rest.save("raw_spans_open_workflow", raw_open.json())
        if first_trace is not None and first_tool is not None:
            break
        time.sleep(2)
    print(f"trace visible after {first_trace}s, tool span after {first_tool}s, workflow still open: {worker.is_alive()}")
    release.set()
    worker.join()
    neatlogs.flush()
    neatlogs.shutdown()
    released = time.time()
    finalized_after = None
    if trace_id:
        while time.time() - released < 240:
            sp = c.get(f"/traces/{trace_id}/spans")
            if sp.status_code == 200:
                finalized_after = time.time() - released
                break
            time.sleep(3)
        print(f"spans endpoint 200 after workflow end: {finalized_after}s")
        trace = c.get(f"/traces/{trace_id}").json()
        _rest.save("trace", trace)
        spans = c.get(f"/traces/{trace_id}/spans").json()
        _rest.save("spans", spans)
        print("trace keys:", list((trace.get("data") or trace).keys()))
        print("session/tags:", (trace.get("data") or trace).get("sessionId"), (trace.get("data") or trace).get("tags"))
        span_items = (spans.get("data") or {}).get("spans") or []
        for s in span_items:
            sid = s.get("spanId") or s.get("id")
            if "day1_notes_write" in str(s.get("name")):
                payload = c.get(f"/traces/{trace_id}/spans/{sid}/payload", params={"fields": "input,output"})
                _rest.save("payload", payload.json())
                print("tool span raw keys:", list(s.keys()))
        raw = c.get(f"/traces/{trace_id}/raw-spans")
        _rest.save("raw_spans", raw.json())
print("TRACE_ID", trace_id)


if trace_id:
    asyncio.run(mcp_check(trace_id))
