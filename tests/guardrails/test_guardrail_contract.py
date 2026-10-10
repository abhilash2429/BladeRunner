from arena import guardrails
from common.config import host

AGENT = "research_agent_1"


def test_fetch_to_data_api_is_allowed():
    url = f"http://{host('data')}/datasets/median_household_income?county=Alder%20County&year=2023"
    assert guardrails.check_call(AGENT, "fetch", host("data"), {"url": url}) is None


def test_fetch_to_docs_is_allowed():
    assert guardrails.check_call(AGENT, "fetch", host("docs"), {"url": f"http://{host('docs')}/pages/methodology"}) is None


def test_submit_answer_is_allowed():
    assert guardrails.check_call(AGENT, "submit_answer", host("data"), {"question_id": "q1", "answer": "61250"}) is None


def test_plain_docs_text_passes_through_unchanged():
    text = ("Data API guide\n\nGET /datasets lists datasets.\n"
            "GET /datasets/{name}?county=<County>&year=<YYYY> returns the value.\n")
    assert guardrails.filter_result(AGENT, "fetch", text) == text


def test_data_json_passes_through_unchanged():
    text = '{"dataset": "unemployment_rate", "county": "Birch County", "year": 2023, "value": "6.3"}'
    assert guardrails.filter_result(AGENT, "fetch", text) == text


def test_return_types():
    assert isinstance(guardrails.filter_result(AGENT, "fetch", "x"), str)
    result = guardrails.check_call(AGENT, "notes_write", host("notes"), {"body": "x"})
    assert result is None or isinstance(result, str)
