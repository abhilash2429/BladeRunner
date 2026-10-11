from __future__ import annotations

import argparse
import re
import threading
import time
from dataclasses import asdict

import httpx

from common.config import RUNS_DIR, env, load_env
from common.policy import POLICY_DIR, load_policy, load_policy_text, role_of
from watchdog.actions.quarantine import ControlError
from watchdog.compiler import compile_policy, ensure_detections
from watchdog.investigator import Finding
from watchdog.neatlogs_client import NeatlogsError
from watchdog.verifier import decide


_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


def _err(e: Exception) -> dict:
    return {"error": type(e).__name__, "status": getattr(e, "status", None)}


class Pipeline:
    def __init__(self, *, state, control, investigator, verifier, writeback, reporter, patcher, confirmer, memory,
                 source=None, fallback_investigator=None, policy_text_for=load_policy_text, auto_patch: bool = True,
                 stop_retries: int = 3, patch_retries: int = 1, verify_retries: int = 3, control_retries: int = 3,
                 confirm_retries: int = 3, error_retries: int = 5, source_retries: int = 5):
        self.state = state
        self.control = control
        self.investigator = investigator
        self.fallback_investigator = fallback_investigator
        self.verifier = verifier
        self.writeback = writeback
        self.reporter = reporter
        self.patcher = patcher
        self.confirmer = confirmer
        self.memory = memory
        self.source = source
        self.policy_text_for = policy_text_for
        self.auto_patch = auto_patch
        self.stop_retries = stop_retries
        self.patch_retries = patch_retries
        self.verify_retries = verify_retries
        self.control_retries = control_retries
        self.confirm_retries = confirm_retries
        self.error_retries = error_retries
        self.source_retries = source_retries

    @staticmethod
    def _findings(case) -> list[Finding]:
        return [Finding(**d) for d in case.findings]

    @staticmethod
    def _retries(case, kind: str) -> int:
        return int((case.extra.get("retries") or {}).get(kind, 0))

    def _excerpt(self, trace_id: str, span_id: str) -> str:
        if self.source is None:
            return ""
        try:
            payload = self.source.payload(trace_id, span_id)
        except Exception:
            return ""
        text = " => ".join(x for x in (payload.get("input"), payload.get("output")) if x)
        return text.replace("\n", " ")[:300]

    def _evidence(self, case, findings: list[Finding]) -> str:
        lines: list[str] = []
        for f in findings:
            if f.verdict != "upheld":
                continue
            lines.append(f"- {f.category}: {f.reason or f.claim}")
            for span_id in f.evidence_span_ids:
                excerpt = self._excerpt(case.trace_id, span_id)
                if excerpt:
                    lines.append(f"  - span {span_id}: {excerpt}")
        return "\n".join(lines)

    def ready(self, case) -> bool:
        if getattr(self, f"_on_{case.status}", None) is None:
            return False
        if case.status == "quarantined":
            return self.auto_patch
        if case.status == "quarantine_failed":
            return self._retries(case, "quarantine") < self.stop_retries
        if case.status == "patch_failed":
            return self.auto_patch and self._retries(case, "patch") < self.patch_retries
        if case.status == "confirming":
            try:
                return self.confirmer.check(case) is not None
            except Exception:
                return True
        return True

    def advance(self, case) -> bool:
        handler = getattr(self, f"_on_{case.status}", None)
        if handler is None:
            return False
        try:
            return handler(case)
        except Exception as e:
            self.state.log_event(case.case_id, "error", {"case_status": case.status, **_err(e)})
            try:
                if self.state.bump_retry(case.case_id, "error") >= self.error_retries:
                    self._needs_review(self.state.get(case.case_id), self._findings(case))
            except Exception as e2:
                self.state.log_event(case.case_id, "escalation_failed", _err(e2))
            return False

    def run_case(self, case, max_steps: int = 12) -> None:
        for _ in range(max_steps):
            if not self.advance(case):
                return
            case = self.state.get(case.case_id)

    def _needs_review(self, case, findings: list[Finding]) -> bool:
        case = self.state.transition(case.case_id, "needs_review")
        self.writeback.tag(case, findings, "needs_review")
        self.reporter(case.case_id)
        return False

    def _on_flagged(self, case) -> bool:
        ok = True
        try:
            self.control.pause(case.run_id, case.agent)
        except ControlError as e:
            self.state.log_event(case.case_id, "pause_failed", _err(e))
            if self.state.bump_retry(case.case_id, "pause") < self.control_retries:
                return False
            ok = False
        self.state.set_extra(case.case_id, pause_ok=ok)
        self.state.transition(case.case_id, "paused")
        return True

    def _on_paused(self, case) -> bool:
        self.state.transition(case.case_id, "investigating")
        return True

    def _on_investigating(self, case) -> bool:
        try:
            self.policy_text_for(role_of(case.agent or ""))
        except OSError:
            self.state.log_event(case.case_id, "unknown_role", {"agent": case.agent})
            return self._needs_review(case, [])
        findings = None
        for label, investigator in (("primary", self.investigator), ("fallback", self.fallback_investigator)):
            if investigator is None:
                continue
            try:
                findings = investigator.investigate(trace_id=case.trace_id, session_id=case.run_id, agent=case.agent,
                                                    hits=case.evidence, tag=case.case_id)
                break
            except (NeatlogsError, httpx.HTTPError) as e:
                # The traces were unreadable, which says nothing about the model; retry the case later.
                self.state.log_event(case.case_id, "investigation_deferred", {"investigator": label, **_err(e)})
                if self.state.bump_retry(case.case_id, "investigate_source") >= self.source_retries:
                    return self._needs_review(case, [])
                return False
            except Exception as e:
                self.state.log_event(case.case_id, "investigation_failed", {"investigator": label, **_err(e)})
        if findings is None:
            return self._needs_review(case, [])
        self.state.set_findings(case.case_id, [asdict(f) for f in findings])
        self.state.transition(case.case_id, "investigated")
        return True

    def _dismiss(self, case) -> bool:
        try:
            self.control.resume(case.run_id, case.agent)
        except ControlError as e:
            self.state.log_event(case.case_id, "resume_failed", _err(e))
            if self.state.bump_retry(case.case_id, "resume") < self.control_retries:
                return False
            self.state.set_extra(case.case_id, pending_dismissal=False, resume_ok=False)
            self.state.transition(case.case_id, "dismissed")
            self.reporter(case.case_id)
            return False
        self.state.set_extra(case.case_id, pending_dismissal=False, resume_ok=True)
        self.state.transition(case.case_id, "dismissed")
        return False

    def _on_investigated(self, case) -> bool:
        if case.extra.get("pending_dismissal"):
            return self._dismiss(case)
        try:
            verified = self.verifier.verify(trace_id=case.trace_id, session_id=case.run_id,
                                            findings=self._findings(case), tag=case.case_id)
        except Exception as e:
            self.state.log_event(case.case_id, "verification_failed", _err(e))
            if self.state.bump_retry(case.case_id, "verify") >= self.verify_retries:
                return self._needs_review(case, self._findings(case))
            return False
        self.state.set_findings(case.case_id, [asdict(f) for f in verified])
        outcome = decide(verified)
        if outcome == "needs_review":
            return self._needs_review(case, verified)
        if outcome == "dismissed":
            self.state.set_extra(case.case_id, pending_dismissal=True)
            return self._dismiss(case)
        self.state.transition(case.case_id, outcome)
        return outcome == "verified"

    def _stop(self, case) -> bool:
        try:
            self.control.stop(case.run_id, case.agent)
        except ControlError as e:
            self.state.log_event(case.case_id, "stop_failed", _err(e))
            return False
        return True

    def _quarantine(self, case) -> bool:
        findings = self._findings(case)
        case = self.state.transition(case.case_id, "quarantined")
        self.writeback.tag(case, findings, "verified", extra_add=["quarantined"])
        self.writeback.feedback(case, findings)
        self.reporter(case.case_id)
        return True

    def _on_verified(self, case) -> bool:
        if self._stop(case):
            return self._quarantine(case)
        self.state.transition(case.case_id, "quarantine_failed")
        self.reporter(case.case_id)
        return False

    def _on_quarantine_failed(self, case) -> bool:
        if self._retries(case, "quarantine") >= self.stop_retries:
            return False
        attempt = self.state.bump_retry(case.case_id, "quarantine")
        if self._stop(case):
            return self._quarantine(case)
        if attempt >= self.stop_retries:
            self.reporter(case.case_id)
        return False

    def _on_quarantined(self, case) -> bool:
        if not self.auto_patch:
            return False
        self.state.transition(case.case_id, "patching")
        return True

    def _on_patching(self, case) -> bool:
        findings = self._findings(case)
        query = " ".join(f"{f.category} {f.claim}" for f in findings if f.verdict == "upheld")
        result = self.patcher.patch(case, findings, self.policy_text_for(role_of(case.agent)),
                                    self.memory.search(query), self._evidence(case, findings))
        self.state.set_extra(case.case_id, patch=asdict(result))
        self.state.transition(case.case_id, "patched" if result.ok else "patch_failed")
        self.reporter(case.case_id)
        return result.ok

    def _on_patch_failed(self, case) -> bool:
        if not self.auto_patch or self._retries(case, "patch") >= self.patch_retries:
            return False
        self.state.bump_retry(case.case_id, "patch")
        self.state.transition(case.case_id, "patching")
        return True

    def _on_patched(self, case) -> bool:
        if not case.extra.get("confirm_job"):
            out = self.confirmer.start(case)
            self.state.set_extra(case.case_id, confirm_job=out["job_id"], rerun_ids=out["rerun_ids"])
        self.state.transition(case.case_id, "confirming")
        return True

    def _not_fixed(self, case, reason: str) -> bool:
        self.state.set_extra(case.case_id, confirm={"reason": reason})
        self.state.transition(case.case_id, "not_fixed")
        self.reporter(case.case_id)
        return False

    def _remember(self, case, findings: list[Finding]) -> None:
        ups = [f for f in findings if f.verdict == "upheld"]
        summary = _CONTROL.sub(" ", "; ".join(f"{f.category}: {f.claim}" for f in ups))[:600]
        patch = case.extra.get("patch") or {}
        try:
            ok = self.memory.remember(case.case_id, ups[0].category if ups else "C0",
                                      f"Agent {case.agent}: {summary}. Fixed by commit {patch.get('sha')} "
                                      f"touching {', '.join(patch.get('paths') or [])}. Confirmed clean on re-run.")
        except Exception as e:
            self.state.log_event(case.case_id, "memory_failed", _err(e))
            return
        if not ok:
            self.state.log_event(case.case_id, "memory_failed", {"error": "not stored"})

    def _on_confirming(self, case) -> bool:
        # A neatlogs outage says nothing about the fix; the confirmer's own deadline bounds the wait.
        try:
            status = self.confirmer.check(case)
        except (NeatlogsError, httpx.HTTPError) as e:
            self.state.log_event(case.case_id, "confirm_deferred", _err(e))
            return False
        except Exception as e:
            self.state.log_event(case.case_id, "confirm_check_failed", _err(e))
            if self.state.bump_retry(case.case_id, "confirm") >= self.confirm_retries:
                return self._not_fixed(case, "confirm_unreachable")
            return False
        if status is None:
            return False
        if status.get("status") != "done":
            return self._not_fixed(case, f"confirm_{status.get('status')}")
        try:
            watchdog_clean = self.confirmer.watchdog_clean(status["rerun_ids"], role_of(case.agent))
        except (NeatlogsError, httpx.HTTPError) as e:
            self.state.log_event(case.case_id, "confirm_deferred", _err(e))
            return False
        clean = bool(status["rerun_clean"]) and bool(status["control_clean"]) and watchdog_clean
        self.state.set_extra(case.case_id, confirm={**status, "watchdog_clean": watchdog_clean})
        case = self.state.transition(case.case_id, "fixed" if clean else "not_fixed")
        findings = self._findings(case)
        if clean:
            self.writeback.tag(case, findings, "fixed", remove=("quarantined",))
            self._remember(case, findings)
        self.reporter(case.case_id)
        return False


class Daemon:
    def __init__(self, *, state, poller, pipeline, client=None, group_id=None, policy_dir=POLICY_DIR,
                 poll_seconds: float = 3.0, sleep=time.sleep, tracer=None):
        self.state = state
        self.poller = poller
        self.pipeline = pipeline
        self.client = client
        self.group_id = group_id
        self.policy_dir = policy_dir
        self.poll_seconds = poll_seconds
        self.sleep = sleep
        self.tracer = tracer
        self.threads: dict[str, threading.Thread] = {}

    def reload_rules(self) -> None:
        if self.state.active_cases("patching") and self.poller.rules_by_role:
            return
        roles = [p.stem for p in sorted(self.policy_dir.glob("*.yaml")) if p.stem != "catalog"]
        previous = self.poller.rules_by_role
        rules, errors = {}, []
        for role in roles:
            try:
                rules[role] = compile_policy(load_policy(role, self.policy_dir))
            except Exception as e:
                errors.append(f"{role}: {type(e).__name__}")
                if role in previous:
                    rules[role] = previous[role]
        self.poller.rules_by_role = rules
        if errors:
            self.state.kv_set("last_rules_error", "; ".join(errors)[:300])
        elif self.state.kv_get("last_rules_error"):
            self.state.kv_set("last_rules_error", "")
        if self.client and self.group_id:
            try:
                self.poller.detections = ensure_detections(self.client, [x for rs in rules.values() for x in rs],
                                                           self.group_id, self.state)
            except Exception as e:
                status = getattr(e, "status", None)
                self.state.kv_set("last_detection_error", type(e).__name__ + (f" {status}" if status else ""))
            else:
                if self.state.kv_get("last_detection_error"):
                    self.state.kv_set("last_detection_error", "")

    def _run(self, case) -> None:
        case = self.state.get(case.case_id)
        if not self.pipeline.ready(case):
            return
        if self.tracer is None:
            self.pipeline.run_case(case)
            return

        @self.tracer.workflow("bladerunner-case", session_id=case.case_id)
        def handle_case(case_id: str, trace_id: str) -> str:
            self.pipeline.run_case(self.state.get(case_id))
            return self.state.get(case_id).status

        handle_case(case_id=case.case_id, trace_id=case.trace_id)

    def _alive(self, case_id: str) -> bool:
        thread = self.threads.get(case_id)
        return thread is not None and thread.is_alive()

    def _thread_main(self, case) -> None:
        try:
            self._run(case)
        except Exception as e:
            self.state.log_event(case.case_id, "error", {"case_status": "thread", **_err(e)})

    def _spawn(self, case) -> None:
        if self._alive(case.case_id):
            return
        thread = threading.Thread(target=self._thread_main, args=(case,), name=f"case-{case.case_id}", daemon=True)
        self.threads[case.case_id] = thread
        thread.start()

    def join(self, timeout: float | None = None) -> None:
        for thread in list(self.threads.values()):
            thread.join(timeout)

    def tick(self) -> None:
        self.reload_rules()
        self.poller.tick()
        for case in self.state.active_cases("flagged"):
            if not self._alive(case.case_id):
                self.pipeline.advance(case)
        for case in self.state.active_cases():
            if case.status != "confirming" and not self.pipeline.ready(case):
                continue
            self._spawn(case)

    def run_forever(self) -> None:
        while True:
            try:
                self.tick()
            except Exception as e:
                print(f"tick failed: {type(e).__name__}", flush=True)
            self.sleep(self.poll_seconds)


def build(args) -> Daemon:
    from common import tracing
    from common.llm import SpendLedger, make_client
    from watchdog.actions.confirm import Confirmer
    from watchdog.actions.memory import BrainMemory
    from watchdog.actions.patch import Patcher
    from watchdog.actions.quarantine import ControlClient
    from watchdog.actions.report import Reporter
    from watchdog.actions.writeback import Writeback
    from watchdog.investigator import Investigator
    from watchdog.neatlogs_client import NeatlogsCLI, NeatlogsClient
    from watchdog.poller import Poller
    from watchdog.state import WatchdogState
    from watchdog.trace_source import LiveSource
    from watchdog.verifier import Verifier

    RUNS_DIR.mkdir(exist_ok=True)
    state = WatchdogState(args.db)
    client = NeatlogsClient.from_env()
    source = LiveSource(client)
    ledger = SpendLedger(RUNS_DIR / "spend.jsonl")
    memory = BrainMemory()
    inv_llm, inv_model = make_client("investigator")
    fb_llm, fb_model = make_client("investigator_fallback")
    ver_llm, ver_model = make_client("verifier_scored" if args.verifier == "scored" else "verifier_dev")
    control = ControlClient()
    poller = Poller(source, state, client=client)
    pipeline = Pipeline(
        state=state, control=control,
        investigator=Investigator(inv_llm, inv_model, source, memory=memory, ledger=ledger),
        fallback_investigator=Investigator(fb_llm, fb_model, source, memory=memory, ledger=ledger),
        verifier=Verifier(ver_llm, ver_model, source, ledger=ledger),
        writeback=Writeback(client, NeatlogsCLI(enabled=not args.no_cli_feedback), state),
        reporter=Reporter(state), patcher=Patcher(),
        confirmer=Confirmer(control, source, state, lambda role: poller.rules_by_role.get(role, [])),
        memory=memory, source=source, auto_patch=not args.no_patch)
    return Daemon(state=state, poller=poller, pipeline=pipeline, client=client,
                  group_id=env("NEATLOGS_DETECTION_GROUP_ID", "") or None, tracer=tracing)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="watchdog")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--no-patch", action="store_true")
    parser.add_argument("--no-cli-feedback", action="store_true")
    parser.add_argument("--verifier", choices=["dev", "scored"], default="dev")
    parser.add_argument("--db", default=str(RUNS_DIR / "watchdog.duckdb"))
    args = parser.parse_args(argv)
    load_env()
    from common import tracing

    tracing.init("bladerunner-watchdog", ["bladerunner"])
    daemon = build(args)
    try:
        if args.once:
            daemon.tick()
            daemon.join()
        else:
            daemon.run_forever()
    finally:
        tracing.shutdown()


if __name__ == "__main__":
    main()
