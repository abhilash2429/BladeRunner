import json
import os
import shutil
import subprocess
import sys

from common.config import env, load_env

load_env()
import _rest  # noqa: E402

trace_id = sys.argv[1]
with _rest.client() as c:
    t = c.get(f"/traces/{trace_id}").json()
    data = t.get("data") or t
    rev = data.get("metadataRevision")
    print("tags before:", data.get("tags"), "revision:", rev)
    p = c.patch(f"/traces/{trace_id}", headers={"idempotency-key": _rest.idem()},
                json={"expectedRevision": rev, "tags": ["day1-probe"], "traceName": "day1 probe"})
    print("PATCH:", p.status_code, p.text[:300])
    after = c.get(f"/traces/{trace_id}").json()
    print("tags after (replace or merge?):", (after.get("data") or after).get("tags"))
    fb = c.post(f"/traces/{trace_id}/feedback", headers={"idempotency-key": _rest.idem()},
                json={"rating": -10, "comment": "service account probe"})
    print("feedback with service account:", fb.status_code, fb.text[:300])
    th = c.get(f"/traces/{trace_id}/comment-threads")
    print("comment threads:", th.status_code, th.text[:200])

venv_scripts = os.path.dirname(sys.executable)
node_path = os.pathsep.join(p for p in os.environ["PATH"].split(os.pathsep) if os.path.normcase(p) != os.path.normcase(venv_scripts))
exe = shutil.which("neatlogs", path=node_path)
print("neatlogs CLI:", exe)
CLI_ENV_ALLOW = ("PATH", "SYSTEMROOT", "APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOME", "TEMP", "TMP")
cli_env = {k: v for k, v in os.environ.items() if k.upper() in CLI_ENV_ALLOW}
cli_env["NEATLOGS_HOST"] = env("NEATLOGS_HOST")
cli_env["NEATLOGS_PROJECT_ID"] = env("NEATLOGS_PROJECT_ID")
status = subprocess.run([exe, "auth", "status"], capture_output=True, text=True, env=cli_env)
print("cli auth status:", status.stdout.strip(), status.stderr.strip())
res = subprocess.run(
    [exe, "feedback", "create", "--trace", trace_id, "--body-stdin", "--idempotency-key", _rest.idem(), "--json"],
    input=json.dumps({"rating": -50, "comment": "day1 CLI probe", "issueTypes": ["C1"]}),
    capture_output=True, text=True, env=cli_env,
)
print("cli feedback:", res.returncode, res.stdout[:300], res.stderr[:300])
