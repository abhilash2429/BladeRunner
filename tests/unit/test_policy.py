from dataclasses import replace

from common.policy import (
    Policy,
    load_catalog,
    load_policy,
    role_of,
    tightening_violations,
)


def test_load_research_policy():
    p = load_policy("research_agent")
    assert p.agent == "research_agent"
    assert "fetch" in p.tools
    assert p.allowed_hosts("fetch") == ("127.0.0.1:8101", "127.0.0.1:8103")
    assert p.allowed_hosts("notes_write") == ()
    assert p.allowed_hosts("unknown_tool") == ()


def test_role_of_strips_numeric_suffix():
    assert role_of("research_agent_3") == "research_agent"
    assert role_of("research_agent") == "research_agent"


def test_catalog_has_seven_categories():
    cat = load_catalog()
    assert sorted(cat) == ["C1", "C2", "C3", "C4", "C5", "C6", "C7"]
    assert cat["C6"]["scored"] is False


def test_tightening_accepts_removals():
    old = load_policy("research_agent")
    new = replace(old, tools=("fetch", "submit_answer"),
                  destinations={"fetch": ("127.0.0.1:8101",), "submit_answer": ("127.0.0.1:8101",)})
    assert tightening_violations(old, new) == []


def test_tightening_rejects_added_tool_and_host():
    old = load_policy("research_agent")
    new = replace(old, tools=old.tools + ("lookup_cache",),
                  destinations={**old.destinations, "notes_write": ("127.0.0.1:8102",)})
    problems = tightening_violations(old, new)
    assert any("tools added" in x for x in problems)
    assert any("notes_write" in x for x in problems)


def test_tightening_rejects_dropped_sensitive_pattern():
    old = load_policy("research_agent")
    new = replace(old, sensitive=())
    assert any("sensitive" in x for x in tightening_violations(old, new))


def test_from_yaml_text_round_trip():
    p = Policy.from_yaml_text("agent: a\ntools: [x]\ndestinations: {x: ['h:1']}\nscope: s\n")
    assert p.tools == ("x",) and p.allowed_hosts("x") == ("h:1",) and p.sensitive == ()
