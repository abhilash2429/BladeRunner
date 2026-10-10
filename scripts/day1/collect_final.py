import json
import sys
import time

import _rest

trace_id = sys.argv[1]
wait = float(sys.argv[2]) if len(sys.argv) > 2 else 0
deadline = time.time() + wait
with _rest.client() as c:
    while True:
        t = c.get(f"/traces/{trace_id}").json()["data"]
        sp = c.get(f"/traces/{trace_id}/spans")
        print(time.strftime("%H:%M:%S"), t["finalizationStatus"], t["spansCount"], "spans:", sp.status_code)
        if sp.status_code == 200 or time.time() > deadline:
            break
        time.sleep(15)
    if sp.status_code != 200:
        sys.exit(1)
    _rest.save("trace", {"data": t})
    spans = sp.json()
    _rest.save("spans", spans)
    items = (spans.get("data") or {}).get("spans") or []
    print("span keys:", [list(s.keys()) for s in items][:1])
    for s in items:
        sid = s.get("spanId") or s.get("id")
        if "day1_notes_write" in str(s.get("name")):
            payload = c.get(f"/traces/{trace_id}/spans/{sid}/payload", params={"fields": "input,output"})
            print("payload:", payload.status_code, payload.text[:400])
            _rest.save("payload", payload.json())
    raw = c.get(f"/traces/{trace_id}/raw-spans")
    print("raw-spans:", raw.status_code)
    _rest.save("raw_spans", raw.json())
