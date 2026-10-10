from arena import content
from arena.scenarios import clean_scenario_for, list_scenarios, load_scenario


def test_tier1_scenarios_exist_in_both_splits():
    ids = {s.id for s in list_scenarios()}
    assert {"clean_dev", "clean", "planted_dev", "planted", "peer_tip_dev", "peer_tip"} <= ids
    assert all(s.split == "dev" for s in list_scenarios("dev"))


def test_dev_and_heldout_use_different_questions():
    dev = set(load_scenario("planted_dev").questions)
    held = set(load_scenario("planted").questions)
    assert dev and held and not dev & held


def test_clean_scenario_for_matches_split():
    assert clean_scenario_for(load_scenario("planted")) == "clean"
    assert clean_scenario_for(load_scenario("peer_tip_dev")) == "clean_dev"


def test_docs_page_variants():
    text, planted = content.docs_page("methodology", "normal")
    assert "GET /datasets" in text and planted is False
    text, planted = content.docs_page("methodology", "planted_v1")
    assert "8102" in text and planted is True
    assert content.docs_page("missing", "normal") == (None, False)


def test_seed_notes_carry_stale_answers():
    notes = content.seed_notes("peer_tip_v1", ["q1"])
    assert len(notes) == 1
    assert notes[0]["answer"] != content.answer_for("q1")
    assert content.seed_notes(None, ["q1"]) == []


def test_every_question_has_an_answer():
    for qid in content.QUESTIONS:
        assert content.answer_for(qid)
