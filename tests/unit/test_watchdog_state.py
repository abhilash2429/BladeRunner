import threading
import uuid
from dataclasses import asdict

import pytest

from watchdog.state import InvalidTransition, WatchdogState


@pytest.fixture
def st(tmp_path):
    return WatchdogState(str(tmp_path / "watchdog.duckdb"))


HIT = {"rule_id": "research_agent:empty_destination:notes_write", "span_id": "s1"}


def test_open_case_new_then_append(st):
    case, changed = st.open_case("t1", "r1", "research_agent_1", "native", [HIT])
    assert changed and case.status == "flagged" and case.flagged_at is not None
    again, changed = st.open_case("t1", "r1", "research_agent_1", "local", [HIT])
    assert not changed and again.case_id == case.case_id and again.source == "both"
    more, changed = st.open_case("t1", "r1", "research_agent_1", "local", [{"rule_id": "x", "span_id": "s2"}])
    assert changed and len(more.evidence) == 2


def test_transitions_and_timestamps(st):
    case, _ = st.open_case("t1", "r1", "a_1", "native", [HIT])
    for to in ("paused", "investigating", "investigated", "verified", "quarantined"):
        case = st.transition(case.case_id, to)
    assert case.status == "quarantined" and case.paused_at and case.quarantined_at
    kinds = [e["kind"] for e in st.events(case.case_id)]
    assert kinds.count("transition") == 5


def test_invalid_transition_raises(st):
    case, _ = st.open_case("t1", "r1", "a_1", "native", [HIT])
    with pytest.raises(InvalidTransition):
        st.transition(case.case_id, "fixed")


def test_dismissed_case_reopens_on_new_evidence(st):
    case, _ = st.open_case("t1", "r1", "a_1", "native", [HIT])
    for to in ("paused", "investigating", "investigated", "dismissed"):
        st.transition(case.case_id, to)
    reopened, changed = st.open_case("t1", "r1", "a_1", "local", [{"rule_id": "r", "span_id": "s9"}])
    assert changed and reopened.status == "flagged"


def test_findings_extra_and_active(st):
    case, _ = st.open_case("t1", "r1", "a_1", "native", [HIT])
    st.set_findings(case.case_id, [{"category": "C1"}])
    st.set_extra(case.case_id, patch_sha="abc")
    st.set_extra(case.case_id, confirm_job="j1")
    got = st.get(case.case_id)
    assert got.findings == [{"category": "C1"}] and got.extra == {"patch_sha": "abc", "confirm_job": "j1"}
    assert [c.case_id for c in st.active_cases()] == [case.case_id]
    assert st.active_cases("paused") == []
    assert st.case_for_trace("t1").case_id == case.case_id and st.case_for_trace("nope") is None


def test_kv(st):
    assert st.kv_get("k") is None
    st.kv_set("k", "1")
    st.kv_set("k", "2")
    assert st.kv_get("k") == "2"


def test_state_survives_reopen(tmp_path):
    path = str(tmp_path / "w.duckdb")
    s1 = WatchdogState(path)
    case, _ = s1.open_case("t1", "r1", "a_1", "native", [HIT])
    s1.conn.close()
    s2 = WatchdogState(path)
    assert s2.get(case.case_id).trace_id == "t1"


HIT2 = {"rule_id": "r2", "span_id": "s2"}


def _to_dismissed(st, case_id):
    for to in ("paused", "investigating", "investigated", "dismissed"):
        st.transition(case_id, to)


def test_set_findings_unknown_case_raises_before_writing(st):
    with pytest.raises(KeyError):
        st.set_findings("nope", [{"category": "C1"}])
    assert st.events("nope") == []


def test_failed_states_retry_edges(st):
    case, _ = st.open_case("t1", "r1", "a_1", "native", [HIT])
    for to in ("paused", "investigating", "investigated", "verified", "quarantine_failed", "quarantined",
               "patching", "patch_failed", "patching", "patched"):
        case = st.transition(case.case_id, to)
    assert case.status == "patched"


def test_failed_states_are_active(st):
    case, _ = st.open_case("t1", "r1", "a_1", "native", [HIT])
    for to in ("paused", "investigating", "investigated", "verified", "quarantine_failed"):
        st.transition(case.case_id, to)
    assert [c.case_id for c in st.active_cases("quarantine_failed")] == [case.case_id]
    st.transition(case.case_id, "quarantined")
    st.transition(case.case_id, "patching")
    st.transition(case.case_id, "patch_failed")
    assert [c.case_id for c in st.active_cases("patch_failed")] == [case.case_id]
    assert [c.case_id for c in st.active_cases()] == [case.case_id]


def test_dismissed_is_not_active(st):
    case, _ = st.open_case("t1", "r1", "a_1", "native", [HIT])
    _to_dismissed(st, case.case_id)
    assert st.active_cases() == []


def test_bump_retry_counts_per_kind(st):
    case, _ = st.open_case("t1", "r1", "a_1", "native", [HIT])
    assert st.bump_retry(case.case_id, "quarantine") == 1
    assert st.bump_retry(case.case_id, "quarantine") == 2
    assert st.bump_retry(case.case_id, "patch") == 1
    assert st.get(case.case_id).extra == {"retries": {"quarantine": 2, "patch": 1}}


def test_bump_retry_unknown_case_raises(st):
    with pytest.raises(KeyError):
        st.bump_retry("nope", "patch")


def test_reopen_keeps_flagged_at(st):
    case, _ = st.open_case("t1", "r1", "a_1", "native", [HIT])
    _to_dismissed(st, case.case_id)
    reopened, changed = st.open_case("t1", "r1", "a_1", "local", [HIT2])
    assert changed and reopened.status == "flagged"
    assert reopened.flagged_at == case.flagged_at


def test_unknown_case_raises_keyerror(st):
    with pytest.raises(KeyError, match="nope"):
        st.get("nope")
    with pytest.raises(KeyError, match="nope"):
        st.transition("nope", "paused")


def test_hits_deduped_within_one_call(st):
    case, changed = st.open_case("t1", "r1", "a_1", "native", [HIT, HIT])
    assert changed and len(case.evidence) == 1
    again, changed = st.open_case("t1", "r1", "a_1", "local", [HIT2, HIT2, HIT])
    assert changed and len(again.evidence) == 2


def test_case_id_is_full_uuid_hex(st):
    case, _ = st.open_case("t1", "r1", "a_1", "native", [HIT])
    assert len(case.case_id) == 32
    assert uuid.UUID(hex=case.case_id).hex == case.case_id


def test_concurrent_open_case_creates_one_case(st):
    barrier = threading.Barrier(2)
    results = []

    def worker():
        barrier.wait()
        results.append(st.open_case("t1", "r1", "a_1", "native", [HIT]))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(changed for _, changed in results) == [False, True]
    assert len({case.case_id for case, _ in results}) == 1
    assert len(st.all_cases()) == 1


def _snapshot(st):
    return [asdict(c) for c in st.all_cases()], st._rows("SELECT count(*) AS n FROM case_events")[0]["n"]


def _fail_on(st, monkeypatch, kind):
    original = st.log_event

    def log(case_id, k, data=None):
        if k == kind:
            raise RuntimeError(kind)
        return original(case_id, k, data)

    monkeypatch.setattr(st, "log_event", log)


def test_set_findings_rolls_back_when_event_fails(st, monkeypatch):
    case, _ = st.open_case("t1", "r1", "a_1", "native", [HIT])
    st.set_findings(case.case_id, [{"category": "C1"}])
    before = _snapshot(st)
    _fail_on(st, monkeypatch, "findings")
    with pytest.raises(RuntimeError):
        st.set_findings(case.case_id, [{"category": "C2"}])
    assert _snapshot(st) == before
    assert st.get(case.case_id).findings == [{"category": "C1"}]


def test_transition_rolls_back_when_event_fails(st, monkeypatch):
    case, _ = st.open_case("t1", "r1", "a_1", "native", [HIT])
    before = _snapshot(st)
    _fail_on(st, monkeypatch, "transition")
    with pytest.raises(RuntimeError):
        st.transition(case.case_id, "paused")
    assert _snapshot(st) == before
    assert st.get(case.case_id).status == "flagged"


def test_open_case_new_rolls_back_when_event_fails(st, monkeypatch):
    before = _snapshot(st)
    _fail_on(st, monkeypatch, "opened")
    with pytest.raises(RuntimeError):
        st.open_case("t1", "r1", "a_1", "native", [HIT])
    assert _snapshot(st) == before
    assert st.case_for_trace("t1") is None


def test_open_case_append_rolls_back_when_event_fails(st, monkeypatch):
    case, _ = st.open_case("t1", "r1", "a_1", "native", [HIT])
    before = _snapshot(st)
    _fail_on(st, monkeypatch, "evidence")
    with pytest.raises(RuntimeError):
        st.open_case("t1", "r1", "a_1", "local", [HIT2])
    assert _snapshot(st) == before
    assert len(st.get(case.case_id).evidence) == 1


def test_open_case_reopen_rolls_back_when_transition_event_fails(st, monkeypatch):
    case, _ = st.open_case("t1", "r1", "a_1", "native", [HIT])
    _to_dismissed(st, case.case_id)
    before = _snapshot(st)
    _fail_on(st, monkeypatch, "transition")
    with pytest.raises(RuntimeError):
        st.open_case("t1", "r1", "a_1", "local", [HIT2])
    assert _snapshot(st) == before
    got = st.get(case.case_id)
    assert got.status == "dismissed" and len(got.evidence) == 1


def test_set_extra_rejects_retries_key(st):
    case, _ = st.open_case("t1", "r1", "a_1", "native", [HIT])
    st.bump_retry(case.case_id, "patch")
    with pytest.raises(ValueError):
        st.set_extra(case.case_id, retries={"patch": 99})
    assert st.get(case.case_id).extra == {"retries": {"patch": 1}}
    assert st.bump_retry(case.case_id, "patch") == 2
