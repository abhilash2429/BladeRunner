from types import SimpleNamespace

import pytest

from common.llm import BudgetExceeded, SpendLedger, assistant_message, extract_json


def test_ledger_cost_and_totals(tmp_path):
    led = SpendLedger(tmp_path / "spend.jsonl", litellm_cap_usd=1.0)
    usd = led.record("minimax.minimax-m2.5", SimpleNamespace(prompt_tokens=1_000_000, completion_tokens=0), tag="c1")
    assert usd == pytest.approx(0.30)
    led.record("claude-sonnet-4.6", SimpleNamespace(prompt_tokens=0, completion_tokens=10_000), tag="c1")
    assert led.total("bedrock") == pytest.approx(0.30)
    assert led.total("litellm") == pytest.approx(0.15)
    assert led.total(tag="c1") == pytest.approx(0.45)


def test_ledger_handles_missing_usage(tmp_path):
    led = SpendLedger(tmp_path / "spend.jsonl")
    assert led.record("deepseek.v3.2", None) == 0.0


def test_ledger_blocks_litellm_over_cap(tmp_path):
    led = SpendLedger(tmp_path / "spend.jsonl", litellm_cap_usd=0.10)
    led.record("claude-sonnet-4.6", SimpleNamespace(prompt_tokens=0, completion_tokens=10_000))
    with pytest.raises(BudgetExceeded):
        led.check("claude-sonnet-4.6")
    led.check("minimax.minimax-m2.5")


def test_assistant_message_with_tool_calls():
    tc = SimpleNamespace(id="t1", function=SimpleNamespace(name="fetch", arguments='{"url": "x"}'))
    msg = SimpleNamespace(content=None, tool_calls=[tc])
    out = assistant_message(msg)
    assert out == {"role": "assistant", "content": "", "tool_calls": [
        {"id": "t1", "type": "function", "function": {"name": "fetch", "arguments": '{"url": "x"}'}}]}


def test_assistant_message_text_only():
    out = assistant_message(SimpleNamespace(content="hi", tool_calls=None))
    assert out == {"role": "assistant", "content": "hi"}


def test_extract_json_from_wrapped_text():
    assert extract_json('Sure.\n```json\n{"a": [1, 2]}\n```') == {"a": [1, 2]}
    with pytest.raises(ValueError):
        extract_json("no json here")
