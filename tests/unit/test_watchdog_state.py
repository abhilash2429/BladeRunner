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
