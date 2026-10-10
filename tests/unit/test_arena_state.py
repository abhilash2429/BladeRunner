from arena.state import ArenaState


def make_state(tmp_path):
    return ArenaState(str(tmp_path / "truth.duckdb"))


def test_register_run_sets_flags_canaries_and_notes(tmp_path):
    s = make_state(tmp_path)
    out = s.register_run("planted_dev", 7, ["research_agent_1"], {"research_agent": "agent: research_agent"}, "abc",
                         seed_notes=[{"author": "seed", "question_id": "q1", "answer": "1", "body": "tip"}])
    run_id = out["run_id"]
    assert out["canaries"]["research_agent_1"].startswith("BR-CANARY-")
    assert s.get_flag(run_id, "research_agent_1") == "run"
    run = s.run(run_id)
    assert run["scenario"] == "planted_dev" and run["agents"] == ["research_agent_1"]
    assert run["policies"]["research_agent"] == "agent: research_agent"
    notes = s.notes_for(run_id)
    assert len(notes) == 1 and notes[0]["author"] == "seed"


def test_log_and_read_requests_in_order(tmp_path):
    s = make_state(tmp_path)
    run_id = s.register_run("clean_dev", 0, ["a_1"], {}, "x")["run_id"]
    first = s.log_request(run_id=run_id, agent="a_1", step=1, tool="fetch", service="docs",
                          method="GET", path="/pages/methodology", args={}, planted=True)
    s.log_request(run_id=run_id, agent="a_1", step=2, tool="notes_write", service="notes",
                  method="POST", path="/notes", args={"body": "hi"}, response_meta={"note_id": "n1"})
    rows = s.requests_for(run_id)
    assert [r["id"] for r in rows][0] == first
    assert rows[0]["planted"] is True
    assert rows[1]["args"] == {"body": "hi"} and rows[1]["response_meta"] == {"note_id": "n1"}


def test_flags_unknown_run_defaults_to_stop(tmp_path):
    s = make_state(tmp_path)
    assert s.get_flag("nope", "a_1") == "stop"


def test_bump_counts(tmp_path):
    s = make_state(tmp_path)
    assert [s.bump("k"), s.bump("k"), s.bump("j")] == [1, 2, 1]


def test_set_flag_rejects_invalid_value(tmp_path):
    import pytest

    s = make_state(tmp_path)
    run_id = s.register_run("clean_dev", 0, ["a_1"], {}, "x")["run_id"]
    with pytest.raises(ValueError):
        s.set_flag(run_id, "a_1", "halt")
    assert s.get_flag(run_id, "a_1") == "run"


def test_stop_flag_is_sticky_under_concurrent_updates(tmp_path):
    import threading

    s = make_state(tmp_path)
    run_id = s.register_run("clean_dev", 0, ["a_1"], {}, "x")["run_id"]
    s.set_flag(run_id, "a_1", "stop")
    threads = [threading.Thread(target=s.set_flag, args=(run_id, "a_1", v)) for v in ("run", "pause") * 50]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert s.get_flag(run_id, "a_1") == "stop"


def test_set_flag_takes_state_lock(tmp_path):
    import threading

    s = make_state(tmp_path)
    run_id = s.register_run("clean_dev", 0, ["a_1"], {}, "x")["run_id"]
    done = threading.Event()
    s._lock.acquire()
    try:
        t = threading.Thread(target=lambda: (s.set_flag(run_id, "a_1", "pause"), done.set()))
        t.start()
        assert not done.wait(0.2)
    finally:
        s._lock.release()
    t.join(2)
    assert done.is_set() and s.get_flag(run_id, "a_1") == "pause"
