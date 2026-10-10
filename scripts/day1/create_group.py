import _rest

NAME = "bladerunner"

with _rest.client() as c:
    groups = c.get("/detection-groups").json()["data"]["groups"]
    found = next((g for g in groups if g["name"] == NAME), None)
    if found is None:
        r = c.post("/detection-groups", headers={"idempotency-key": _rest.idem()}, json={"name": NAME})
        print("create:", r.status_code)
        found = r.json()["data"]
        found = found.get("group") or found
    print("GROUP_ID", found["id"])
