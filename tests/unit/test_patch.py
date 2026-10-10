import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from common.config import REPO_ROOT
from watchdog.actions.patch import Git, OpenCodeRunner, Patcher, parse_porcelain, path_violations
from watchdog.investigator import Finding

CASE = SimpleNamespace(case_id="c1", trace_id="t1", agent="research_agent_1", run_id="r1")
FINDINGS = [Finding("C1", ["s2"], "wrote to the notes board", 0.9, "upheld", "ok")]
GUARD = "arena/guardrails/__init__.py"
POLICY = "policy/research_agent.yaml"


def sh(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    for rel in (POLICY, "policy/catalog.yaml", GUARD):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text((REPO_ROOT / rel).read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "arena/mocks").mkdir(parents=True)
    (tmp_path / "arena/mocks/x.py").write_text("X = 1\n")
    sh(tmp_path, "init", "-b", "main")
    sh(tmp_path, "config", "user.email", "t@example.com")
    sh(tmp_path, "config", "user.name", "t")
    sh(tmp_path, "config", "core.hooksPath", str(tmp_path / "no-hooks"))
    sh(tmp_path, "add", "-A")
    sh(tmp_path, "commit", "-m", "base")
    return tmp_path


class Runner:
    def __init__(self, *edits):
        self.edits = list(edits)
        self.requests = []

    def __call__(self, fixreq, repo):
        self.requests.append(Path(fixreq).read_text(encoding="utf-8"))
        edit = self.edits.pop(0)
        if edit:
            rel, text = edit
            (Path(repo) / rel).write_text(text, encoding="utf-8")
        return 0, "done"


BLOCK_NOTES = (GUARD, 'def check_call(agent, tool, target_host, args):\n'
                      '    return "notes are off-policy" if tool.startswith("notes_") else None\n\n\n'
                      'def filter_result(agent, tool, text):\n    return text\n')


def patcher(repo, runner, **kw):
    kw.setdefault("test_cmd", [sys.executable, "-c", "pass"])
    kw.setdefault("why_cmd", (sys.executable, "-c", "print('why output')"))
    return Patcher(repo=repo, runner=runner, fixreq_dir=repo / "runs" / "fixreq", **kw)


def test_good_guardrail_patch_commits(repo):
    result = patcher(repo, Runner(BLOCK_NOTES)).patch(CASE, FINDINGS, "agent: research_agent", ["past incident"])
    assert result.ok and result.attempts == 1 and result.paths == [GUARD]
    assert "why output" in result.why
    log = subprocess.run(["git", "log", "-1", "--format=%B"], cwd=repo, capture_output=True, text=True).stdout
    assert "incident c1" in log
    files = subprocess.run(["git", "show", "--name-only", "--format=", "HEAD"], cwd=repo, capture_output=True,
                           text=True).stdout.split()
    assert sorted(files) == [GUARD, "docs/incidents/c1.md"]
    assert "wrote to the notes board" in (repo / "docs" / "incidents" / "c1.md").read_text(encoding="utf-8")


def test_edit_outside_surface_is_rejected_and_reverted(repo):
    bad = ("arena/mocks/x.py", "X = 2\n")
    result = patcher(repo, Runner(bad, bad)).patch(CASE, FINDINGS, "p", [])
    assert not result.ok and "outside the patch surface" in result.reason
    assert Git(repo).changes() == []


def test_widening_policy_is_rejected(repo):
    text = (repo / POLICY).read_text().replace("notes_write: []", 'notes_write: ["127.0.0.1:8102"]')
    result = patcher(repo, Runner((POLICY, text), (POLICY, text))).patch(CASE, FINDINGS, "p", [])
    assert not result.ok and "tightening" in result.reason


def test_tightening_policy_is_accepted(repo):
    text = (repo / POLICY).read_text().replace("tools: [fetch, submit_answer, notes_read, notes_write]",
                                               "tools: [fetch, submit_answer]")
    assert patcher(repo, Runner((POLICY, text))).patch(CASE, FINDINGS, "p", []).ok


def test_no_changes_fails(repo):
    result = patcher(repo, Runner(None, None)).patch(CASE, FINDINGS, "p", [])
    assert not result.ok and result.reason == "no files changed"


def test_second_attempt_gets_failure_reason(repo):
    runner = Runner(("arena/mocks/x.py", "X = 3\n"), BLOCK_NOTES)
    result = patcher(repo, runner).patch(CASE, FINDINGS, "p", [])
    assert result.ok and result.attempts == 2
    assert "outside the patch surface" in runner.requests[1]


def test_dirty_tree_refuses(repo):
    (repo / "arena/mocks/x.py").write_text("X = 9\n")
    result = patcher(repo, Runner(BLOCK_NOTES)).patch(CASE, FINDINGS, "p", [])
    assert not result.ok and result.attempts == 0 and "not clean" in result.reason


def test_fix_request_carries_evidence_and_brain_filter_rule(repo):
    runner = Runner(BLOCK_NOTES)
    patcher(repo, runner).patch(CASE, FINDINGS, "p", [], evidence="- C1: s2 is notes_write\n  - span s2: body=hello")
    request = runner.requests[0]
    assert "## Evidence" in request and "span s2: body=hello" in request
    assert "use only results whose path starts with docs/incidents/" in request


def test_patch_calls_are_serialized(repo):
    class SlowRunner:
        def __init__(self):
            self.lock = threading.Lock()
            self.active = 0
            self.peak = 0

        def __call__(self, fixreq, repo_path):
            with self.lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
            time.sleep(0.05)
            with self.lock:
                self.active -= 1
            return 0, "done"

    runner = SlowRunner()
    p = patcher(repo, runner)
    threads = [threading.Thread(target=p.patch, args=(CASE, FINDINGS, "p", [])) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert runner.peak == 1


def test_path_violations_and_porcelain():
    assert path_violations([GUARD, POLICY, "policy/catalog.yaml", "watchdog/x.py"]) == [
        "policy/catalog.yaml", "watchdog/x.py"]
    assert parse_porcelain(" M a/b.py\n?? c.txt\nR  old.py -> new.py\n") == [
        (" M", "a/b.py"), ("??", "c.txt"), ("R ", "new.py")]


def test_commit_names_the_patch_model(repo):
    runner = Runner(BLOCK_NOTES)
    runner.model = "amazon-bedrock/zai.glm-5"
    assert patcher(repo, runner).patch(CASE, FINDINGS, "p", []).ok
    log = subprocess.run(["git", "log", "-1", "--format=%B"], cwd=repo, capture_output=True, text=True).stdout
    assert log.strip().splitlines()[-1] == "Co-Authored-By: OpenCode amazon-bedrock/zai.glm-5 <noreply@opencode.ai>"


def test_test_timeout_is_a_failed_attempt_not_a_crash(repo):
    slow = [sys.executable, "-c", "import time; time.sleep(5)"]
    result = patcher(repo, Runner(BLOCK_NOTES, BLOCK_NOTES), test_cmd=slow, test_timeout=0.2).patch(
        CASE, FINDINGS, "p", [])
    assert not result.ok and result.attempts == 2 and "timed out" in result.reason
    assert Git(repo).changes() == []


def test_missing_why_binary_still_commits(repo):
    result = patcher(repo, Runner(BLOCK_NOTES), why_cmd=("definitely-not-a-real-binary",)).patch(
        CASE, FINDINGS, "p", [])
    assert result.ok and result.why == ""


def test_failed_commit_reverts_and_reports(repo, tmp_path_factory):
    hooks = tmp_path_factory.mktemp("hooks")
    (hooks / "pre-commit").write_text("#!/bin/sh\nexit 1\n")
    (hooks / "pre-commit").chmod(0o755)
    sh(repo, "config", "core.hooksPath", str(hooks))
    result = patcher(repo, Runner(BLOCK_NOTES)).patch(CASE, FINDINGS, "p", [])
    assert not result.ok and "commit failed" in result.reason
    assert Git(repo).changes() == []
    staged = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=repo, capture_output=True, text=True)
    assert staged.stdout == ""


def test_runner_reports_missing_executable():
    code, out = OpenCodeRunner(exe="definitely-not-a-real-binary")(Path("x.md"), Path("."))
    assert code == -1 and out == "FileNotFoundError"


def test_runner_ignores_configured_exe_that_does_not_exist(monkeypatch, tmp_path):
    exe = tmp_path / "opencode"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.setenv("OPENCODE_EXE", r"C:\nowhere\opencode.exe")
    monkeypatch.setenv("PATH", str(tmp_path))
    assert OpenCodeRunner().exe == str(exe)
    monkeypatch.setenv("OPENCODE_EXE", str(exe))
    assert OpenCodeRunner().exe == str(exe)


def test_runner_passes_fix_request_file_and_model():
    seen = {}

    def run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    code, out = OpenCodeRunner(exe="oc", run=run)(Path("runs/fixreq/c1.md"), Path("/repo"))
    assert code == 0 and out == "ok"
    assert seen["cmd"] == ["oc", "run", "--format", "json", "-m", "amazon-bedrock/zai.glm-5", "-f",
                           "runs/fixreq/c1.md", "Apply the attached fix request."]
    assert seen["kw"]["cwd"] == Path("/repo") and seen["kw"]["timeout"] == 600 and "PATH" in seen["kw"]["env"]
