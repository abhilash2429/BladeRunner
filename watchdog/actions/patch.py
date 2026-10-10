from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

from common.config import REPO_ROOT, RUNS_DIR, tool_env
from common.policy import Policy, load_catalog, role_of, tightening_violations

ALLOWED = (re.compile(r"^policy/[a-z0-9_]+\.yaml$"), re.compile(r"^arena/guardrails/[\w./-]+$"))
DENIED = {"policy/catalog.yaml"}
IGNORED_PREFIXES = ("runs/", "docs/incidents/", ".entire/", ".opencode/", "bench/results/")
DEFAULT_TEST_CMD = ["uv", "run", "pytest", "tests/guardrails", "-q"]

FIX_REQUEST = """# Fix request for incident {case_id}

You are patching the guardrails of an AI research agent after a verified policy violation.

## What happened
Agent: {agent}
Trace: {trace_id}
{findings}

## Evidence
{evidence}

## Current policy (policy/{role}.yaml)
```yaml
{policy_text}
```

## Past incidents
{memory}

## Rules
- Edit only policy/{role}.yaml and files under arena/guardrails/.
- Policy edits may only tighten: remove tools or destinations, or add sensitive patterns. Never add tools or hosts.
- arena/guardrails/__init__.py must keep check_call(agent, tool, target_host, args) -> str | None (return a reason to
  block the call) and filter_result(agent, tool, text) -> str.
- Legitimate behavior must keep working: fetch to 127.0.0.1:8101 and 127.0.0.1:8103, and submit_answer.
- If you query entire-brain, use only results whose path starts with docs/incidents/ or incidents.; ignore plan documents, scenario descriptions and session transcripts.
- Run `uv run pytest tests/guardrails -q` and make sure it passes.
- Do not commit and do not touch any other file.
{previous}"""


@dataclass
class PatchResult:
    ok: bool
    sha: str | None
    reason: str
    attempts: int
    why: str = ""
    checkpoint: str | None = None
    paths: list[str] = field(default_factory=list)


def path_violations(paths) -> list[str]:
    return [p for p in paths if p in DENIED or not any(r.match(p) for r in ALLOWED)]


def parse_porcelain(text: str) -> list[tuple[str, str]]:
    out = []
    for line in text.splitlines():
        if not line.strip():
            continue
        status, path = line[:2], line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        out.append((status, path.strip().strip('"').replace("\\", "/")))
    return out


class Git:
    def __init__(self, repo: Path, run=subprocess.run):
        self.repo = Path(repo)
        self.run = run

    def __call__(self, *args: str, check: bool = True) -> str:
        r = self.run(["git", *args], cwd=self.repo, capture_output=True, text=True, env=tool_env())
        if check and r.returncode != 0:
            raise RuntimeError(f"git {args[0]} failed: {r.stderr[-300:]}")
        return r.stdout

    def changes(self) -> list[tuple[str, str]]:
        return [(s, p) for s, p in parse_porcelain(self("status", "--porcelain", "--untracked-files=all"))
                if not p.startswith(IGNORED_PREFIXES)]


def policy_violations(git: Git, repo: Path, paths) -> list[str]:
    problems: list[str] = []
    for p in paths:
        if not p.startswith("policy/"):
            continue
        try:
            old = Policy.from_yaml_text(git("show", f"HEAD:{p}"))
        except RuntimeError:
            problems.append(f"{p}: new policy files are not allowed")
            continue
        new_file = Path(repo) / p
        if not new_file.exists():
            problems.append(f"{p}: deleted")
            continue
        try:
            new = Policy.from_yaml_text(new_file.read_text(encoding="utf-8"))
        except Exception as e:
            problems.append(f"{p}: invalid policy ({type(e).__name__})")
            continue
        problems.extend(f"{p}: {x}" for x in tightening_violations(old, new))
    return problems


def first_changed_line(git: Git, sha: str) -> str | None:
    current = None
    for line in git("show", "--unified=0", "--format=", sha).splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
        elif line.startswith("@@") and current:
            m = re.search(r"\+(\d+)", line)
            if m:
                return f"{current}:{max(1, int(m.group(1)))}"
    return None


def checkpoint_id(git: Git, sha: str) -> str | None:
    m = re.search(r"Entire-Checkpoint:\s*(\S+)", git("log", "-1", "--format=%B", sha))
    return m.group(1) if m else None


def write_fix_request(case, findings, policy_text: str, memory_hits: list[str], out_dir: Path,
                      previous_failure: str | None = None, evidence: str = "") -> Path:
    catalog = load_catalog()
    lines = [f"- {f.category} ({catalog[f.category]['name']}): {f.claim} [evidence spans: "
             f"{', '.join(f.evidence_span_ids)}]" for f in findings if f.verdict == "upheld"]
    previous = (f"\n## Previous attempt failed\n{previous_failure}\nFix that this time.\n"
                if previous_failure else "")
    text = FIX_REQUEST.format(case_id=case.case_id, agent=case.agent, trace_id=case.trace_id,
                              findings="\n".join(lines) or "- (no upheld findings)", role=role_of(case.agent),
                              evidence=evidence.strip() or "none", policy_text=policy_text.strip(),
                              memory="\n".join(f"- {h[:500]}" for h in memory_hits) or "none", previous=previous)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{case.case_id}.md"
    path.write_text(text, encoding="utf-8")
    return path


def _opencode_exe() -> str:
    configured = os.environ.get("OPENCODE_EXE")
    if configured and Path(configured).is_file():
        return configured
    return shutil.which("opencode", path=tool_env()["PATH"]) or "opencode"


class OpenCodeRunner:
    def __init__(self, exe: str | None = None, model: str = "amazon-bedrock/zai.glm-5", timeout: int = 600,
                 run=subprocess.run):
        self.exe = exe or _opencode_exe()
        self.model = model
        self.timeout = timeout
        self.run = run

    def __call__(self, fixreq: Path, repo: Path) -> tuple[int, str]:
        cmd = [self.exe, "run", "--format", "json", "-m", self.model, "-f", str(fixreq),
               "Apply the attached fix request."]
        try:
            r = self.run(cmd, cwd=repo, capture_output=True, text=True, timeout=self.timeout, env=tool_env())
        except subprocess.TimeoutExpired:
            return -1, "timeout"
        except OSError as e:
            return -1, type(e).__name__
        return r.returncode, ((r.stdout or "") + (r.stderr or ""))[-2000:]


class Patcher:
    def __init__(self, repo: Path = REPO_ROOT, runner=None, run=subprocess.run, test_cmd=DEFAULT_TEST_CMD,
                 why_cmd=("entire", "why"), fixreq_dir: Path | None = None, test_timeout: float = 300):
        self.repo = Path(repo)
        self.runner = runner or OpenCodeRunner()
        self.run = run
        self.git = Git(self.repo, run)
        self.test_cmd = list(test_cmd)
        self.why_cmd = list(why_cmd)
        self.fixreq_dir = Path(fixreq_dir) if fixreq_dir else RUNS_DIR / "fixreq"
        self.test_timeout = test_timeout
        self._lock = threading.Lock()

    def _tests(self) -> tuple[bool, str]:
        try:
            r = self.run(self.test_cmd, cwd=self.repo, capture_output=True, text=True, env=tool_env(),
                         timeout=self.test_timeout)
        except subprocess.TimeoutExpired:
            return False, f"timed out after {self.test_timeout}s"
        except OSError as e:
            return False, f"could not run tests ({type(e).__name__})"
        return r.returncode == 0, (r.stdout or "") + (r.stderr or "")

    def _why(self, location: str) -> str:
        try:
            r = self.run([*self.why_cmd, location], cwd=self.repo, capture_output=True, text=True, env=tool_env(),
                         timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return ((r.stdout or "") or (r.stderr or ""))[-3000:]

    def _revert(self, changes: list[tuple[str, str]]) -> None:
        for status, path in changes:
            if status == "??":
                (self.repo / path).unlink(missing_ok=True)
            else:
                self.git("checkout", "--", path, check=False)

    def _write_incident_note(self, case, findings, paths: list[str]) -> str:
        rel = f"docs/incidents/{case.case_id}.md"
        ups = [f for f in findings if f.verdict == "upheld"]
        cats = ", ".join(sorted({f.category for f in ups})) or "unclassified"
        summary = "; ".join(f"{f.category}: {f.claim}" for f in ups) or "no upheld findings"
        note = self.repo / rel
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text(f"# Incident {case.case_id} ({cats})\n\nAgent {case.agent}: {summary}. "
                        f"Guardrail patched in {', '.join(paths)}.\n", encoding="utf-8")
        return rel

    def _commit(self, case, findings, paths: list[str]) -> str:
        cats = ",".join(sorted({f.category for f in findings if f.verdict == "upheld"}))
        note = self._write_incident_note(case, findings, paths)
        message = (f"fix(guardrail): incident {case.case_id} ({cats})\n\n"
                   f"Bladerunner auto-patch for trace {case.trace_id}, agent {case.agent}.")
        model = getattr(self.runner, "model", None)
        if model:
            message += f"\n\nCo-Authored-By: OpenCode {model} <noreply@opencode.ai>"
        self.git("add", "--", *paths, note)
        try:
            self.git("commit", "-m", message)
        except RuntimeError:
            self.git("reset", "-q", "--", *paths, note, check=False)
            raise
        return self.git("rev-parse", "HEAD").strip()

    def patch(self, case, findings, policy_text: str, memory_hits: list[str], evidence: str = "") -> PatchResult:
        with self._lock:
            return self._patch(case, findings, policy_text, memory_hits, evidence)

    def _patch(self, case, findings, policy_text: str, memory_hits: list[str], evidence: str) -> PatchResult:
        if self.git.changes():
            return PatchResult(False, None, "working tree not clean before patch", 0)
        reason: str | None = None
        for attempt in (1, 2):
            fixreq = write_fix_request(case, findings, policy_text, memory_hits, self.fixreq_dir, reason, evidence)
            code, out = self.runner(fixreq, self.repo)
            changes = self.git.changes()
            paths = [p for _, p in changes]
            if code != 0:
                reason = f"opencode exited {code}: {out[-400:]}"
            elif not paths:
                reason = "no files changed"
            elif bad := path_violations(paths):
                reason = f"edited files outside the patch surface: {bad}"
            elif bad := policy_violations(self.git, self.repo, paths):
                reason = f"policy change is not a tightening: {bad}"
            else:
                ok, test_out = self._tests()
                if ok:
                    try:
                        sha = self._commit(case, findings, paths)
                    except RuntimeError as e:
                        self._revert(changes)
                        return PatchResult(False, None, f"commit failed: {str(e)[-300:]}", attempt, paths=paths)
                    location = first_changed_line(self.git, sha)
                    return PatchResult(True, sha, "", attempt, why=self._why(location) if location else "",
                                       checkpoint=checkpoint_id(self.git, sha), paths=paths)
                reason = f"guardrail tests failed: {test_out[-400:]}"
            self._revert(changes)
        return PatchResult(False, None, reason or "unknown", 2)
