import threading
from dataclasses import replace

from watchdog.actions.patch import PatchResult
from watchdog.actions.quarantine import ControlError
from watchdog.daemon import Daemon, Pipeline
from watchdog.investigator import Finding
from watchdog.state import WatchdogState


class FakeControl:
    def __init__(self, stop_fails=0, pause_fails=0, resume_fails=0):
        self.calls = []
        self.stop_fails = stop_fails
        self.pause_fails = pause_fails
        self.resume_fails = resume_fails

    def pause(self, run_id, agent):
        self.calls.append("pause")
        if self.pause_fails:
            self.pause_fails -= 1
            raise ControlError("down")

    def resume(self, run_id, agent):
        self.calls.append("resume")
        if self.resume_fails:
            self.resume_fails -= 1
            raise ControlError("down")

    def stop(self, run_id, agent):
        self.calls.append("stop")
        if self.stop_fails:
            self.stop_fails -= 1
            raise ControlError("down")


class FakeInvestigator:
    def __init__(self, error=False, claim="wrote notes"):
        self.error = error
        self.claim = claim
        self.calls = 0

    def investigate(self, **kw):
        self.calls += 1
        if self.error:
            raise RuntimeError("model down")
        return [Finding("C1", ["s2"], self.claim, 0.9)]


class FakeVerifier:
    def __init__(self, verdict, fail=0):
        self.verdict = verdict
        self.fail = fail

    def verify(self, *, findings, **kw):
        if self.fail:
            self.fail -= 1
            raise RuntimeError("verifier down")
        return [replace(f, verdict=self.verdict, reason="r") for f in findings]


class FakeWriteback:
    def __init__(self):
        self.tags = []
        self.feedback_calls = 0

    def tag(self, case, findings, status_tag, extra_add=(), remove=()):
        self.tags.append(status_tag)
        return True

    def feedback(self, case, findings):
        self.feedback_calls += 1


class FakePatcher:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = 0

    def patch(self, case, findings, policy_text, memory_hits, evidence=""):
        self.calls += 1
        self.evidence = evidence
        ok = self.ok(self.calls) if callable(self.ok) else self.ok
        if ok:
            return PatchResult(True, "abc", "", 1, why="why", checkpoint="cp1", paths=["arena/guardrails/__init__.py"])
        return PatchResult(False, None, "no files changed", 2)


class FakeSpanSource:
    def payload(self, trace_id, span_id):
        return {"input": '{"tool": "notes_write", "args": {"body": "hello"}}', "output": "x" * 400}


class FakeConfirmer:
    def __init__(self, clean=True, ready_after=1, outcome=None):
        self.clean = clean
        self.ready_after = ready_after
        self.outcome = outcome
        self.checks = 0
        self.starts = 0

    def start(self, case):
        self.starts += 1
        return {"job_id": "j1", "rerun_ids": ["x1"]}

    def check(self, case):
        self.checks += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        if self.checks <= self.ready_after:
            return None
        if self.outcome:
            return self.outcome
        return {"status": "done", "rerun_clean": self.clean, "control_clean": True, "rerun_ids": ["x1"]}

    def watchdog_clean(self, rerun_ids, role):
        return True


class FakeMemory:
    def __init__(self, fail=False):
        self.remembered = []
        self.texts = []
        self.fail = fail

    def search(self, query, k=5):
        return []

    def remember(self, case_id, category, text):
        if self.fail:
            raise OSError("read-only")
        self.remembered.append(case_id)
        self.texts.append(text)
        return True


def policy_text(role):
    if role != "research_agent":
        raise FileNotFoundError(role)
    return "agent: research_agent"


def build(tmp_path, *, verdict="upheld", control=None, investigator=None, fallback=None, verifier=None,
          patcher=None, confirmer=None, auto_patch=True, source=None, agent="research_agent_1", memory=None):
    st = WatchdogState(str(tmp_path / "w.duckdb"))
    reports = []
    parts = dict(control=control or FakeControl(), writeback=FakeWriteback(), memory=memory or FakeMemory(),
                 confirmer=confirmer or FakeConfirmer())
    p = Pipeline(state=st, investigator=investigator or FakeInvestigator(), fallback_investigator=fallback,
                 verifier=verifier or FakeVerifier(verdict), reporter=reports.append,
                 patcher=patcher or FakePatcher(), policy_text_for=policy_text, auto_patch=auto_patch,
                 source=source, **parts)
    case, _ = st.open_case("t1", "r1", agent, "native", [{"rule_id": "r", "span_id": "s2"}])
    return p, st, case, parts, reports


def test_full_path_to_fixed(tmp_path):
    p, st, case, parts, reports = build(tmp_path)
    p.run_case(case)
    assert st.get(case.case_id).status == "confirming"
    p.run_case(st.get(case.case_id))
    final = st.get(case.case_id)
    assert final.status == "fixed"
    assert parts["control"].calls == ["pause", "stop"]
    assert parts["writeback"].tags == ["verified", "fixed"] and parts["writeback"].feedback_calls == 1
    assert parts["memory"].remembered == [case.case_id]
    assert final.extra["patch"]["sha"] == "abc" and final.extra["confirm"]["watchdog_clean"] is True
    assert reports.count(case.case_id) >= 3


def test_refuted_finding_dismisses_and_resumes(tmp_path):
    p, st, case, parts, _ = build(tmp_path, verdict="refuted")
    p.run_case(case)
    assert st.get(case.case_id).status == "dismissed"
    assert parts["control"].calls == ["pause", "resume"]


def test_downgraded_needs_review(tmp_path):
    p, st, case, parts, _ = build(tmp_path, verdict="downgraded")
    p.run_case(case)
    assert st.get(case.case_id).status == "needs_review"
    assert parts["writeback"].tags == ["needs_review"] and "stop" not in parts["control"].calls


def test_stop_is_retried_three_times_then_reported(tmp_path):
    p, st, case, parts, reports = build(tmp_path, control=FakeControl(stop_fails=4))
    p.run_case(case)
    assert st.get(case.case_id).status == "quarantine_failed"
    for _ in range(5):
        current = st.get(case.case_id)
        if p.ready(current):
            p.run_case(current)
    final = st.get(case.case_id)
    assert final.status == "quarantine_failed" and final.extra["retries"]["quarantine"] == 3
    assert parts["control"].calls.count("stop") == 4 and not p.ready(final)
    assert reports.count(case.case_id) == 2


def test_stop_retry_recovers_to_quarantined(tmp_path):
    p, st, case, parts, _ = build(tmp_path, control=FakeControl(stop_fails=1), auto_patch=False)
    p.run_case(case)
    assert st.get(case.case_id).status == "quarantine_failed"
    p.run_case(st.get(case.case_id))
    assert st.get(case.case_id).status == "quarantined"
    assert parts["writeback"].tags == ["verified"] and parts["writeback"].feedback_calls == 1


def test_no_auto_patch_stops_at_quarantined(tmp_path):
    p, st, case, _, _ = build(tmp_path, auto_patch=False)
    p.run_case(case)
    assert st.get(case.case_id).status == "quarantined"
    assert not p.ready(st.get(case.case_id))


def test_patch_failure(tmp_path):
    p, st, case, _, _ = build(tmp_path, patcher=FakePatcher(ok=False))
    p.run_case(case)
    final = st.get(case.case_id)
    assert final.status == "patch_failed" and final.extra["patch"]["reason"] == "no files changed"


def test_patch_failure_is_retried_once(tmp_path):
    patcher = FakePatcher(ok=False)
    p, st, case, _, _ = build(tmp_path, patcher=patcher)
    p.run_case(case)
    assert p.ready(st.get(case.case_id))
    p.run_case(st.get(case.case_id))
    final = st.get(case.case_id)
    assert final.status == "patch_failed" and patcher.calls == 2 and not p.ready(final)
    p.run_case(final)
    assert patcher.calls == 2


def test_patch_retry_can_succeed(tmp_path):
    p, st, case, _, _ = build(tmp_path, patcher=FakePatcher(ok=lambda n: n > 1))
    p.run_case(case)
    p.run_case(st.get(case.case_id))
    assert st.get(case.case_id).status == "confirming"


def test_not_fixed(tmp_path):
    p, st, case, _, _ = build(tmp_path, confirmer=FakeConfirmer(clean=False))
    p.run_case(case)
    p.run_case(st.get(case.case_id))
    assert st.get(case.case_id).status == "not_fixed"


def test_patching_passes_evidence_to_patcher(tmp_path):
    patcher = FakePatcher()
    p, st, case, _, _ = build(tmp_path, patcher=patcher, source=FakeSpanSource())
    p.run_case(case)
    assert "C1: r" in patcher.evidence
    assert "span s2:" in patcher.evidence and "notes_write" in patcher.evidence
    assert "x" * 301 not in patcher.evidence


def test_investigator_error_falls_back_to_second_model(tmp_path):
    fallback = FakeInvestigator()
    p, st, case, _, _ = build(tmp_path, investigator=FakeInvestigator(error=True), fallback=fallback)
    p.run_case(case)
    assert fallback.calls == 1 and st.get(case.case_id).status == "confirming"
    failed = [e for e in st.events(case.case_id) if e["kind"] == "investigation_failed"]
    assert failed[0]["data"] == {"investigator": "primary", "error": "RuntimeError", "status": None}


def test_both_investigators_failing_needs_review_never_dismissed(tmp_path):
    p, st, case, parts, _ = build(tmp_path, investigator=FakeInvestigator(error=True),
                                  fallback=FakeInvestigator(error=True))
    p.run_case(case)
    assert st.get(case.case_id).status == "needs_review"
    assert parts["control"].calls == ["pause"] and parts["writeback"].tags == ["needs_review"]
    assert len([e for e in st.events(case.case_id) if e["kind"] == "investigation_failed"]) == 2


def test_unknown_role_needs_review_without_investigating(tmp_path):
    investigator = FakeInvestigator()
    p, st, case, _, _ = build(tmp_path, investigator=investigator, agent="mystery_bot_1")
    p.run_case(case)
    assert st.get(case.case_id).status == "needs_review" and investigator.calls == 0
    assert any(e["kind"] == "unknown_role" for e in st.events(case.case_id))


def test_uncertain_claim_downgraded_by_verifier_goes_to_review(tmp_path):
    p, st, case, parts, _ = build(tmp_path, verdict="downgraded",
                                  investigator=FakeInvestigator(claim="Uncertain: maybe notes"))
    p.run_case(case)
    assert st.get(case.case_id).status == "needs_review" and "stop" not in parts["control"].calls


def test_verifier_errors_retry_then_need_review(tmp_path):
    p, st, case, _, _ = build(tmp_path, verifier=FakeVerifier("upheld", fail=5))
    p.run_case(case)
    assert st.get(case.case_id).status == "investigated"
    p.run_case(st.get(case.case_id))
    assert st.get(case.case_id).status == "investigated"
    p.run_case(st.get(case.case_id))
    assert st.get(case.case_id).status == "needs_review"


def test_verifier_error_then_success_continues(tmp_path):
    p, st, case, _, _ = build(tmp_path, verifier=FakeVerifier("upheld", fail=1), auto_patch=False)
    p.run_case(case)
    p.run_case(st.get(case.case_id))
    assert st.get(case.case_id).status == "quarantined"


def test_errors_log_type_not_message(tmp_path):
    class Leaky:
        def investigate(self, **kw):
            return [Finding("C1", ["s2"], "x", 0.9)]

    class LeakyVerifier:
        def verify(self, **kw):
            raise RuntimeError("SECRET-BODY")

    p, st, case, _, _ = build(tmp_path, investigator=Leaky(), verifier=LeakyVerifier())
    p.run_case(case)
    assert "SECRET-BODY" not in str(st.events(case.case_id))


class FakePoller:
    def __init__(self):
        self.ticks = 0
        self.rules_by_role = {}
        self.detections = {}

    def tick(self):
        self.ticks += 1
        return []


def test_daemon_reload_and_tick(tmp_path):
    p, st, case, _, _ = build(tmp_path)
    poller = FakePoller()
    d = Daemon(state=st, poller=poller, pipeline=p)
    d.reload_rules()
    assert "research_agent" in poller.rules_by_role
    d.tick()
    d.join()
    assert poller.ticks == 1 and st.get(case.case_id).status == "confirming"


class FlakyDetectionClient:
    def __init__(self):
        self.fail = False
        self.created = []

    def create_detection(self, spec, idempotency_key):
        if self.fail:
            raise RuntimeError("400 invalid detection spec")
        self.created.append(spec["displayName"])
        return {"id": f"det-{len(self.created)}"}


def test_reload_rules_keeps_previous_detections_on_error(tmp_path):
    p, st, _, _, _ = build(tmp_path)
    poller = FakePoller()
    client = FlakyDetectionClient()
    d = Daemon(state=st, poller=poller, pipeline=p, client=client, group_id="g")
    old = {"det-old": "research_agent:empty_destination:notes_write"}
    poller.detections = dict(old)
    client.fail = True
    d.reload_rules()
    assert poller.detections == old and "research_agent" in poller.rules_by_role
    assert st.kv_get("last_detection_error") == "RuntimeError"
    d.tick()
    d.join()
    assert poller.ticks == 1
    client.fail = False
    d.reload_rules()
    assert poller.detections != old and not st.kv_get("last_detection_error")


def test_first_detection_error_leaves_watchdog_on_local_rules(tmp_path):
    p, st, _, _, _ = build(tmp_path)
    poller = FakePoller()
    client = FlakyDetectionClient()
    client.fail = True
    Daemon(state=st, poller=poller, pipeline=p, client=client, group_id="g").reload_rules()
    assert poller.detections == {} and "research_agent" in poller.rules_by_role


class BlockingPipeline:
    def __init__(self):
        self.release = threading.Event()
        self.started = threading.Event()
        self.runs = []

    def ready(self, case):
        return True

    def advance(self, case):
        return False

    def run_case(self, case):
        self.runs.append(case.case_id)
        self.started.set()
        self.release.wait(5)


def test_daemon_runs_cases_in_threads_and_keeps_polling(tmp_path):
    _, st, case, _, _ = build(tmp_path)
    poller = FakePoller()
    pipe = BlockingPipeline()
    d = Daemon(state=st, poller=poller, pipeline=pipe)
    d.tick()
    assert pipe.started.wait(5)
    d.tick()
    assert poller.ticks == 2 and pipe.runs == [case.case_id]
    pipe.release.set()
    d.join()
    d.tick()
    d.join()
    assert pipe.runs == [case.case_id, case.case_id]


class RecordingTracer:
    def __init__(self):
        self.spans = []

    def workflow(self, name, session_id):
        def decorate(fn):
            def wrapped(*args, **kwargs):
                self.spans.append(name)
                return fn(*args, **kwargs)

            return wrapped

        return decorate


def test_waiting_case_does_not_open_a_case_span(tmp_path):
    p, st, case, _, _ = build(tmp_path, confirmer=FakeConfirmer(ready_after=99))
    p.run_case(case)
    assert st.get(case.case_id).status == "confirming"
    tracer = RecordingTracer()
    d = Daemon(state=st, poller=FakePoller(), pipeline=p, tracer=tracer)
    d._run(st.get(case.case_id))
    assert tracer.spans == [] and st.get(case.case_id).status == "confirming"
    p.confirmer.ready_after = 0
    d._run(st.get(case.case_id))
    assert tracer.spans == ["bladerunner-case"] and st.get(case.case_id).status == "fixed"


def test_exhausted_case_is_not_rerun_by_daemon(tmp_path):
    p, st, case, parts, _ = build(tmp_path, control=FakeControl(stop_fails=99))
    d = Daemon(state=st, poller=FakePoller(), pipeline=p)
    for _ in range(8):
        d.tick()
        d.join()
    assert parts["control"].calls.count("stop") == 4
    d.threads.clear()
    d.tick()
    assert case.case_id not in d.threads


def to_confirming(tmp_path, confirmer, **kw):
    p, st, case, parts, reports = build(tmp_path, confirmer=confirmer, **kw)
    p.run_case(case)
    assert st.get(case.case_id).status == "confirming"
    return p, st, st.get(case.case_id), parts, reports


def test_failed_confirm_job_is_not_fixed(tmp_path):
    p, st, case, _, reports = to_confirming(tmp_path, FakeConfirmer(outcome={"status": "error"}))
    p.run_case(case)
    final = st.get(case.case_id)
    assert final.status == "not_fixed" and final.extra["confirm"]["reason"] == "confirm_error"
    assert reports[-1] == case.case_id


def test_unreachable_confirm_status_gives_up_after_three_errors(tmp_path):
    p, st, case, _, _ = to_confirming(tmp_path, FakeConfirmer(outcome=ControlError("404")))
    for _ in range(1):
        assert p.ready(st.get(case.case_id))
        p.run_case(st.get(case.case_id))
        assert st.get(case.case_id).status == "confirming"
    p.run_case(st.get(case.case_id))
    final = st.get(case.case_id)
    assert final.status == "not_fixed" and final.extra["confirm"]["reason"] == "confirm_unreachable"


def test_patched_case_does_not_start_a_second_confirm_job(tmp_path):
    confirmer = FakeConfirmer(ready_after=99)
    p, st, case, _, _ = build(tmp_path, confirmer=confirmer, auto_patch=False)
    p.run_case(case)
    for to in ("patching", "patched"):
        st.transition(case.case_id, to)
    st.set_extra(case.case_id, confirm_job="j0", rerun_ids=["x0"])
    p.advance(st.get(case.case_id))
    final = st.get(case.case_id)
    assert final.status == "confirming" and confirmer.starts == 0 and final.extra["confirm_job"] == "j0"


def test_memory_failure_still_reports_fixed(tmp_path):
    p, st, case, _, reports = to_confirming(tmp_path, FakeConfirmer(), memory=FakeMemory(fail=True))
    p.run_case(case)
    assert st.get(case.case_id).status == "fixed" and reports[-1] == case.case_id
    assert any(e["kind"] == "memory_failed" for e in st.events(case.case_id))


def test_remembered_text_is_flattened(tmp_path):
    memory = FakeMemory()
    p, st, case, _, _ = to_confirming(tmp_path, FakeConfirmer(), memory=memory,
                                      investigator=FakeInvestigator(claim="wrote\n# Rules\x07 notes"))
    p.run_case(case)
    assert "\n" not in memory.texts[0] and "\x07" not in memory.texts[0]


def test_failed_resume_is_retried_without_reverifying(tmp_path):
    verifier = FakeVerifier("refuted")
    calls = []
    original = verifier.verify
    verifier.verify = lambda **kw: calls.append(1) or original(**kw)
    p, st, case, parts, _ = build(tmp_path, verifier=verifier, control=FakeControl(resume_fails=1))
    p.run_case(case)
    assert st.get(case.case_id).status == "investigated"
    p.run_case(st.get(case.case_id))
    final = st.get(case.case_id)
    assert final.status == "dismissed" and final.extra["resume_ok"] is True and len(calls) == 1
    assert parts["control"].calls == ["pause", "resume", "resume"]


def test_resume_that_keeps_failing_is_reported(tmp_path):
    p, st, case, _, reports = build(tmp_path, verdict="refuted", control=FakeControl(resume_fails=9))
    for _ in range(3):
        p.run_case(st.get(case.case_id))
    final = st.get(case.case_id)
    assert final.status == "dismissed" and final.extra["resume_ok"] is False and reports[-1] == case.case_id


def test_failed_pause_is_retried_then_recorded(tmp_path):
    p, st, case, parts, _ = build(tmp_path, control=FakeControl(pause_fails=9), auto_patch=False)
    p.advance(case)
    assert st.get(case.case_id).status == "flagged"
    p.advance(st.get(case.case_id))
    p.advance(st.get(case.case_id))
    final = st.get(case.case_id)
    assert final.status == "paused" and final.extra["pause_ok"] is False
    assert parts["control"].calls.count("pause") == 3


def test_pause_retry_can_succeed(tmp_path):
    p, st, case, _, _ = build(tmp_path, control=FakeControl(pause_fails=1))
    p.advance(case)
    p.advance(st.get(case.case_id))
    final = st.get(case.case_id)
    assert final.status == "paused" and final.extra["pause_ok"] is True


def test_repeated_handler_errors_escalate_to_review(tmp_path):
    class Broken(FakePatcher):
        def patch(self, *a, **kw):
            raise RuntimeError("boom")

    p, st, case, _, reports = build(tmp_path, patcher=Broken())
    p.run_case(case)
    for _ in range(4):
        assert st.get(case.case_id).status == "patching"
        p.run_case(st.get(case.case_id))
    final = st.get(case.case_id)
    assert final.status == "needs_review" and reports[-1] == case.case_id
    errors = [e for e in st.events(case.case_id) if e["kind"] == "error"]
    assert errors[0]["data"] == {"case_status": "patching", "error": "RuntimeError", "status": None}


def test_bad_policy_file_keeps_that_roles_previous_rules(tmp_path):
    from common.policy import POLICY_DIR

    pdir = tmp_path / "policy"
    pdir.mkdir()
    (pdir / "research_agent.yaml").write_text((POLICY_DIR / "research_agent.yaml").read_text())
    (pdir / "other_agent.yaml").write_text("tools: [")
    p, st, _, _, _ = build(tmp_path)
    poller = FakePoller()
    poller.rules_by_role = {"other_agent": ["old-rule"]}
    d = Daemon(state=st, poller=poller, pipeline=p, policy_dir=pdir)
    d.reload_rules()
    assert poller.rules_by_role["other_agent"] == ["old-rule"] and poller.rules_by_role["research_agent"]
    assert st.kv_get("last_rules_error").startswith("other_agent")
    (pdir / "other_agent.yaml").unlink()
    d.reload_rules()
    assert "other_agent" not in poller.rules_by_role and not st.kv_get("last_rules_error")


def test_rules_are_not_reloaded_while_a_patch_is_in_progress(tmp_path):
    p, st, case, _, _ = build(tmp_path, auto_patch=False)
    p.run_case(case)
    st.transition(case.case_id, "patching")
    poller = FakePoller()
    poller.rules_by_role = {"research_agent": ["frozen"]}
    Daemon(state=st, poller=poller, pipeline=p).reload_rules()
    assert poller.rules_by_role == {"research_agent": ["frozen"]}
