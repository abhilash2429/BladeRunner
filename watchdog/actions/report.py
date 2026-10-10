from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import quote

from jinja2 import Environment

from common.config import RUNS_DIR

DEFAULT_TRACE_URL = "https://app.neatlogs.com/traces/{trace_id}"
CELL_LIMIT = 500
WHY_LIMIT = 4000
EVENT_KEYS = ("to", "from", "ok", "span_id", "status", "categories")
EVIDENCE_KEYS = ("rule_id", "span_id", "kind")
_ESCAPE = re.compile(r"([\\`*_\[\]<>!()])")

TEMPLATE = """# Incident {{ case.case_id | md_cell }}: {{ title | md_cell }}

- Status: {{ case.status | md_cell }}
- Agent: {{ case.agent | md_cell }}
- Run: {{ case.run_id | md_cell }}
- Trace: [{{ case.trace_id | md_cell }}]({{ url }})
- Detected by: {{ case.source | md_cell }}
- Flagged: {{ case.flagged_at | md_cell }}
- Paused: {{ (case.paused_at or "not paused") | md_cell }}
- Quarantined: {{ (case.quarantined_at or "not quarantined") | md_cell }}

## Findings

| Category | Claim | Evidence spans | Confidence | Verifier |
|---|---|---|---|---|
{% for f in findings -%}
| {{ f.category | md_cell }} | {{ f.claim | md_cell }} | {% for s in f.evidence_span_ids %}[{{ s | md_cell }}]({{ span_url(case.trace_id, s) }}){{ ", " if not loop.last }}{% endfor %} | {{ "%.2f" | format(f.confidence) }} | {{ (f.verdict or "pending") | md_cell }}: {{ (f.reason or "") | md_cell }} |
{% endfor %}
## Fix

{% if patch and patch.ok -%}
- Commit: {{ patch.sha | md_cell }}
- Entire checkpoint: {{ (patch.checkpoint or "not found") | md_cell }}
- Files: {{ (patch.paths or []) | map("md_cell") | join(", ") }}

entire why on the first patched line:

{{ why_fence }}
{{ why }}
{{ why_fence }}
{% elif patch -%}
Patch failed after {{ patch.attempts | md_cell }} attempt(s): {{ patch.reason | md_cell }}
{% else -%}
No patch applied.
{% endif %}
## Confirmation

{% if confirm -%}
- Re-runs clean in ground truth: {{ confirm.rerun_clean | md_cell }}
- Clean-scenario control run passed: {{ confirm.control_clean | md_cell }}
- Watchdog view of re-runs clean: {{ confirm.watchdog_clean | md_cell }}
{% else -%}
Not confirmed yet.
{% endif %}
## Timeline

{% for e in events -%}
- {{ e.ts | md_cell }} {{ e.kind | md_cell }}{% if e.data %} ({% for k, v in e.data.items() %}{{ k | md_cell }}={{ v | md_cell }}{{ ", " if not loop.last }}{% endfor %}){% endif %}
{% endfor %}"""


def md_cell(value, limit: int = CELL_LIMIT) -> str:
    text = "" if value is None else str(value)
    text = re.sub(r"[\r\n]+", " ", text).replace("\u2014", ", ").replace("|", "/")
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return _ESCAPE.sub(r"\\\1", text)


def fence_for(text: str) -> str:
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def trace_url(trace_id: str) -> str:
    template = os.environ.get("NEATLOGS_TRACE_URL", DEFAULT_TRACE_URL)
    return template.replace("{trace_id}", quote(str(trace_id), safe=""))


def span_url(trace_id: str, span_id: str) -> str:
    base = trace_url(trace_id)
    return f"{base}{'&' if '?' in base else '?'}span={quote(str(span_id), safe='')}"


def _scalar(value):
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    return value


def _safe_events(events: list[dict]) -> list[dict]:
    out = []
    for e in events:
        data = e["data"] if isinstance(e.get("data"), dict) else {}
        out.append({"ts": e["ts"], "kind": e["kind"],
                    "data": {k: _scalar(data[k]) for k in EVENT_KEYS if k in data}})
    return out


def _safe_evidence(evidence: list[dict]) -> list[dict]:
    return [{k: h.get(k) for k in EVIDENCE_KEYS if k in h} for h in evidence]


def _safe_finding(f: dict) -> dict:
    out = dict(f)
    for k in ("category", "claim", "reason", "verdict"):
        if out.get(k) is not None:
            out[k] = md_cell(out[k])
    out["evidence_span_ids"] = [str(s) for s in out.get("evidence_span_ids", [])]
    return out


def _safe_extra(extra: dict) -> dict:
    out = {}
    patch = extra.get("patch")
    if isinstance(patch, dict):
        out["patch"] = {k: (md_cell(patch[k]) if k == "reason" else patch[k])
                        for k in ("ok", "sha", "checkpoint", "paths", "attempts", "reason") if k in patch}
    if isinstance(extra.get("confirm"), dict):
        out["confirm"] = extra["confirm"]
    return out


class Reporter:
    def __init__(self, state, out_dir: Path = RUNS_DIR / "incidents"):
        self.state = state
        self.out_dir = Path(out_dir)
        env = Environment(autoescape=False)
        env.filters["md_cell"] = md_cell
        self.template = env.from_string(TEMPLATE)

    def __call__(self, case_id: str) -> tuple[Path, Path]:
        case = self.state.get(case_id)
        events = _safe_events(self.state.events(case_id))
        cats = sorted({f["category"] for f in case.findings if f.get("verdict") == "upheld"})
        title = f"{', '.join(cats) or 'under review'} on {case.agent}"
        patch = case.extra.get("patch")
        why = ""
        if isinstance(patch, dict):
            why = str(patch.get("why") or "").replace("\u2014", ", ")[:WHY_LIMIT]
        self.out_dir.mkdir(parents=True, exist_ok=True)
        md = self.out_dir / f"{case_id}.md"
        md.write_text(self.template.render(case=case, findings=case.findings, events=events, title=title,
                                           url=trace_url(case.trace_id), span_url=span_url,
                                           patch=patch, confirm=case.extra.get("confirm"),
                                           why=why, why_fence=fence_for(why)),
                      encoding="utf-8")
        js = self.out_dir / f"{case_id}.json"
        case_data = {"case_id": case.case_id, "trace_id": case.trace_id, "run_id": case.run_id,
                     "agent": case.agent, "status": case.status, "source": case.source,
                     "evidence": _safe_evidence(case.evidence), "flagged_at": case.flagged_at,
                     "paused_at": case.paused_at, "quarantined_at": case.quarantined_at,
                     "extra": _safe_extra(case.extra)}
        js.write_text(json.dumps({"case": case_data, "findings": [_safe_finding(f) for f in case.findings],
                                  "events": events, "trace_url": trace_url(case.trace_id)},
                                 indent=2, default=str), encoding="utf-8")
        return md, js
