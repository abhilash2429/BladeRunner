import time

from common.config import env, load_env

load_env()
import neatlogs  # noqa: E402

import _rest  # noqa: E402

group = env("NEATLOGS_DETECTION_GROUP_ID")
specs = {
    "regex": {
        "type": "regex", "displayName": "day1 regex notes_write", "groupId": group,
        "runOn": {"frequency": "span_name", "span_names": ["day1_notes_write"]},
        "config": {"input_pattern": ".+", "output_pattern": None, "case_sensitive": False, "operator": "OR"},
    },
    "conditional": {
        "type": "conditional", "displayName": "day1 conditional target_host", "groupId": group,
        "runOn": {"frequency": "span_name", "span_names": ["day1_notes_write"]},
        "config": {"conditions": [{"field": "neatlogs.tool.target_host",
                                   "operator": "not_in", "value": ["127.0.0.1:8101"]}], "logic": "AND"},
    },
}
ids = {}
with _rest.client() as c:
    existing = {d["displayName"]: d["id"] for d in c.get("/detections", params={"groupId": group, "limit": 100}).json()["data"]["detections"]}
    for name, spec in specs.items():
        if spec["displayName"] in existing:
            ids[name] = existing[spec["displayName"]]
            print("reuse", name, ids[name])
            continue
        r = c.post("/detections", headers={"idempotency-key": _rest.idem()}, json=spec)
        print("create", name, r.status_code, r.text[:300])
        if r.status_code < 300:
            body = r.json()
            ids[name] = (body.get("data") or body).get("id")

    neatlogs.init(api_key=env("NEATLOGS_INGEST_KEY"), workflow_name="day1-detection", flush_interval=1.0)

    @neatlogs.span(kind="TOOL", name="day1_notes_write", tool_name="day1_notes_write")
    def tool_call(tool, target_host, args):
        try:
            from opentelemetry import trace
            trace.get_current_span().set_attribute("neatlogs.tool.target_host", target_host)
        except ImportError:
            pass
        return "ok"

    emitted = time.time()
    tool_call(tool="day1_notes_write", target_host="127.0.0.1:8102", args={"body": "probe"})
    neatlogs.flush()
    seen = {}
    while time.time() - emitted < 180 and len(seen) < len(ids):
        for name, det_id in ids.items():
            if name in seen:
                continue
            h = c.get(f"/detections/{det_id}/hits", params={"limit": 20}).json()
            hits = (h.get("data") or {}).get("hits") or []
            if hits:
                seen[name] = time.time() - emitted
                _rest.save(f"hits_{name}", h)
        time.sleep(3)
    neatlogs.shutdown()
print("hit latency seconds:", seen, "detection ids:", ids)
