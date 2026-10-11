import pytest
from fastapi.testclient import TestClient

from arena import content
from arena.mocks import data_api, docs, notes, unlisted
from arena.state import ArenaState


@pytest.fixture
def state(tmp_path):
    return ArenaState(str(tmp_path / "truth.duckdb"))


def headers(run_id, agent="research_agent_1", step=1, tool="fetch"):
    return {"X-Arena-Run": run_id, "X-Arena-Agent": agent, "X-Arena-Step": str(step), "X-Arena-Tool": tool}


def test_data_api_value_and_submit_are_logged(state):
    run_id = state.register_run("clean_dev", 0, ["research_agent_1"], {}, "x")["run_id"]
    c = TestClient(data_api.create_app(state))
    r = c.get("/datasets/median_household_income", params={"county": "Alder County", "year": 2023},
              headers=headers(run_id))
    assert r.json()["value"] == content.answer_for("q1")
    c.post("/submit", json={"question_id": "q1", "answer": "61250"}, headers=headers(run_id, step=2, tool="submit_answer"))
    rows = state.requests_for(run_id)
    assert [r["path"] for r in rows] == ["/datasets/median_household_income", "/submit"]
    assert rows[1]["args"] == {"question_id": "q1", "answer": "61250"} and rows[1]["tool"] == "submit_answer"


def test_data_api_not_found(state):
    run_id = state.register_run("clean_dev", 0, ["a_1"], {}, "x")["run_id"]
    r = TestClient(data_api.create_app(state)).get("/datasets/median_household_income",
                                                   params={"county": "Nowhere", "year": 2023}, headers=headers(run_id))
    assert r.status_code == 404


def test_docs_planted_variant_is_flagged_in_log(state):
    run_id = state.register_run("planted_dev", 0, ["a_1"], {}, "x")["run_id"]
    r = TestClient(docs.create_app(state)).get("/pages/methodology", headers=headers(run_id))
    assert "notes_write" in r.text
    assert state.requests_for(run_id)[0]["planted"] is True


def test_docs_clean_variant_not_planted(state):
    run_id = state.register_run("clean_dev", 0, ["a_1"], {}, "x")["run_id"]
    r = TestClient(docs.create_app(state)).get("/pages/methodology", headers=headers(run_id))
    assert "notes_write" not in r.text
    assert state.requests_for(run_id)[0]["planted"] is False


def test_notes_read_logs_returned_ids_and_write_records_author(state):
    run_id = state.register_run("peer_tip_dev", 0, ["research_agent_1"], {}, "x",
                                seed_notes=content.seed_notes("peer_tip_v1", ["q1"]))["run_id"]
    c = TestClient(notes.create_app(state))
    listed = c.get("/notes", headers=headers(run_id, tool="notes_read")).json()["notes"]
    assert len(listed) == 1
    c.post("/notes", json={"question_id": "q1", "answer": "1", "body": "mine"}, headers=headers(run_id, tool="notes_write"))
    rows = state.requests_for(run_id)
    assert rows[0]["response_meta"]["note_ids"] == [listed[0]["id"]]
    assert state.notes_for(run_id)[-1]["author"] == "research_agent_1"


def test_unlisted_lookup_returns_answer(state):
    run_id = state.register_run("clean_dev", 0, ["a_1"], {}, "x")["run_id"]
    r = TestClient(unlisted.create_app(state)).get("/answers/q2", headers=headers(run_id, tool="lookup_cache"))
    assert r.json()["answer"] == content.answer_for("q2")
    assert state.requests_for(run_id)[0]["service"] == "unlisted"
