from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

from jinja2 import Environment

from common.config import RUNS_DIR

DEFAULT_TRACE_URL = "https://app.neatlogs.com/traces/{trace_id}"

TEMPLATE = """# Incident {{ case.case_id }}: {{ title }}

- Status: {{ case.status }}
- Agent: {{ case.agent }}
- Run: {{ case.run_id }}
- Trace: [{{ case.trace_id }}]({{ url }})
- Detected by: {{ case.source }}
- Flagged: {{ case.flagged_at }}
- Paused: {{ case.paused_at or "not paused" }}
- Quarantined: {{ case.quarantined_at or "not quarantined" }}

## Findings

| Category | Claim | Evidence spans | Confidence | Verifier |
|---|---|---|---|---|
{% for f in findings -%}
| {{ f.category }} | {{ f.claim | replace("|", "/") }} | {% for s in f.evidence_span_ids %}[{{ s }}]({{ span_url(case.trace_id, s) }}){{ ", " if not loop.last }}{% endfor %} | {{ "%.2f" | format(f.confidence) }} | {{ f.verdict or "pending" }}: {{ (f.reason or "") | replace("|", "/") }} |
{% endfor %}
## Fix

{% if patch and patch.ok -%}
- Commit: {{ patch.sha }}
- Entire checkpoint: {{ patch.checkpoint or "not found" }}
- Files: {{ (patch.paths or []) | join(", ") }}

entire why on the first patched line:

```
{{ patch.why }}
```
{% elif patch -%}
Patch failed after {{ patch.attempts }} attempt(s): {{ patch.reason }}
{% else -%}
No patch applied.
{% endif %}
## Confirmation

{% if confirm -%}
- Re-runs clean in ground truth: {{ confirm.rerun_clean }}
- Clean-scenario control run passed: {{ confirm.control_clean }}
- Watchdog view of re-runs clean: {{ confirm.watchdog_clean }}
{% else -%}
Not confirmed yet.
{% endif %}
## Timeline

{% for e in events -%}
- {{ e.ts }} {{ e.kind }} {{ e.data | tojson }}
{% endfor %}"""


def trace_url(trace_id: str) -> str:
    return os.environ.get("NEATLOGS_TRACE_URL", DEFAULT_TRACE_URL).format(trace_id=trace_id)


def span_url(trace_id: str, span_id: str) -> str:
    base = trace_url(trace_id)
    return f"{base}{'&' if '?' in base else '?'}span={span_id}"


class Reporter:
    def __init__(self, state, out_dir: Path = RUNS_DIR / "incidents"):
        self.state = state
        self.out_dir = Path(out_dir)
        self.template = Environment(autoescape=False).from_string(TEMPLATE)

    def __call__(self, case_id: str) -> tuple[Path, Path]:
        case = self.state.get(case_id)
        events = self.state.events(case_id)
        cats = sorted({f["category"] for f in case.findings if f.get("verdict") == "upheld"})
        title = f"{', '.join(cats) or 'under review'} on {case.agent}"
        self.out_dir.mkdir(parents=True, exist_ok=True)
        md = self.out_dir / f"{case_id}.md"
        md.write_text(self.template.render(case=case, findings=case.findings, events=events, title=title,
                                           url=trace_url(case.trace_id), span_url=span_url,
                                           patch=case.extra.get("patch"), confirm=case.extra.get("confirm")),
                      encoding="utf-8")
        js = self.out_dir / f"{case_id}.json"
        js.write_text(json.dumps({"case": asdict(case), "findings": case.findings, "events": events,
                                  "trace_url": trace_url(case.trace_id)}, indent=2, default=str), encoding="utf-8")
        return md, js
