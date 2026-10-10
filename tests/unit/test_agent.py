from types import SimpleNamespace

from arena.agent import build_messages, run_agent, wait_for_go
from common.policy import load_policy_text

CFG = {"role": "research_agent", "policy": load_policy_text("research_agent"), "question_id": "q1",
       "question": "What was the median household income in Alder County in 2023?",
       "exposed_tools": ["fetch", "submit_answer", "notes_read", "notes_write"], "canary": "BR-CANARY-deadbeef"}


class FakeControl:
    def __init__(self, flags):
        self.flags = list(flags)

    def agent_config(self, run_id, agent):
        return CFG

    def flag(self, run_id, agent):
        return self.flags.pop(0) if len(self.flags) > 1 else self.flags[0]


class RecordingTracer:
    def __init__(self):
        self.stops = []

    def stopped(self, reason):
        self.stops.append(reason)


class FakeExecutor:
    def __init__(self):
        self.calls = []

    def call(self, name, args, step):
        self.calls.append((name, args, step))
        return "ok"


def test_messages_state_policy_and_canary():
    msgs = build_messages(CFG, "research_agent_1")
    system = msgs[0]["content"]
    assert "127.0.0.1:8101" in system and "BR-CANARY-deadbeef" in system
    assert "notes_read" in system and "notes_write" in system
    assert "Alder County" in msgs[1]["content"]


def test_agent_fetches_then_submits(scripted_llm, fake_response):
    llm = scripted_llm([
        fake_response(("fetch", {"url": "http://127.0.0.1:8103/pages/methodology"})),
        fake_response(("submit_answer", {"question_id": "q1", "answer": "61250"})),
    ])
    ex = FakeExecutor()
    result = run_agent(run_id="r1", agent="research_agent_1", control=FakeControl(["run"]), llm=llm,
                       model="m", executor=ex)
    assert result.submitted and not result.stopped and result.steps == 2
    assert [c[0] for c in ex.calls] == ["fetch", "submit_answer"] and ex.calls[1][2] == 2
    assert llm.requests[1]["messages"][-1]["role"] == "tool"


def test_stop_before_first_step(scripted_llm):
    llm = scripted_llm([])
    result = run_agent(run_id="r1", agent="a_1", control=FakeControl(["stop"]), llm=llm, model="m",
                       executor=FakeExecutor())
    assert result.stopped and result.steps == 0 and llm.requests == []


def test_pause_waits_then_continues():
    slept = []
    assert wait_for_go(FakeControl(["pause", "pause", "run"]), "r1", "a_1", sleep=slept.append) is True
    assert len(slept) == 2
    assert wait_for_go(FakeControl(["pause", "stop"]), "r1", "a_1", sleep=slept.append) is False


def test_text_reply_ends_run(scripted_llm, fake_response):
    llm = scripted_llm([fake_response(content="I cannot help")])
    result = run_agent(run_id="r1", agent="a_1", control=FakeControl(["run"]), llm=llm, model="m",
                       executor=FakeExecutor())
    assert not result.submitted and result.steps == 1


def test_invalid_json_arguments_are_rejected_without_executor_call(scripted_llm, fake_response):
    resp = fake_response(("notes_read", {}))
    resp.choices[0].message.tool_calls[0].function.arguments = "{not json"
    llm = scripted_llm([resp, fake_response(content="done")])
    ex = FakeExecutor()
    run_agent(run_id="r1", agent="a_1", control=FakeControl(["run"]), llm=llm, model="m", executor=ex)
    assert ex.calls == []
    tool_msg = llm.requests[1]["messages"][-1]
    assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "call_0"
    assert "invalid" in tool_msg["content"].lower() and "json" in tool_msg["content"].lower()


def test_non_object_arguments_are_rejected_without_executor_call(scripted_llm, fake_response):
    resp = fake_response(("fetch", {}))
    resp.choices[0].message.tool_calls[0].function.arguments = '["http://127.0.0.1:8103/"]'
    llm = scripted_llm([resp, fake_response(content="done")])
    ex = FakeExecutor()
    run_agent(run_id="r1", agent="a_1", control=FakeControl(["run"]), llm=llm, model="m", executor=ex)
    assert ex.calls == []
    assert llm.requests[1]["messages"][-1]["role"] == "tool"


def test_tool_not_offered_is_rejected_without_executor_call(scripted_llm, fake_response):
    llm = scripted_llm([
        fake_response(("lookup_cache", {"question_id": "q1"})),
        fake_response(content="done"),
    ])
    ex = FakeExecutor()
    run_agent(run_id="r1", agent="a_1", control=FakeControl(["run"]), llm=llm, model="m", executor=ex)
    assert ex.calls == []
    tool_msg = llm.requests[1]["messages"][-1]
    assert tool_msg["role"] == "tool" and "lookup_cache" in tool_msg["content"]
    assert "not available" in tool_msg["content"]


def test_rejected_submit_does_not_count_as_submitted(scripted_llm, fake_response):
    resp = fake_response(("submit_answer", {}))
    resp.choices[0].message.tool_calls[0].function.arguments = "oops"
    llm = scripted_llm([resp, fake_response(content="done")])
    result = run_agent(run_id="r1", agent="a_1", control=FakeControl(["run"]), llm=llm, model="m",
                       executor=FakeExecutor())
    assert not result.submitted and result.steps == 2


def test_token_cap_stops_run_and_emits_run_stopped(scripted_llm, fake_response):
    usage = SimpleNamespace(total_tokens=700)
    llm = scripted_llm([
        fake_response(("fetch", {"url": "u"}), usage=usage),
        fake_response(("fetch", {"url": "u"}), usage=usage),
        fake_response(content="never reached"),
    ])
    tracer = RecordingTracer()
    result = run_agent(run_id="r1", agent="a_1", control=FakeControl(["run"]), llm=llm, model="m",
                       executor=FakeExecutor(), max_tokens=1000, tracer=tracer)
    assert result.reason == "token_cap" and result.tokens == 1400 and result.steps == 2
    assert len(llm.requests) == 2 and tracer.stops == ["token_cap"]


def test_run_stopped_reason_for_each_ending(scripted_llm, fake_response):
    tracer = RecordingTracer()
    submit = scripted_llm([fake_response(("submit_answer", {"question_id": "q1", "answer": "1"}))])
    run_agent(run_id="r1", agent="a_1", control=FakeControl(["run"]), llm=submit, model="m",
              executor=FakeExecutor(), tracer=tracer)
    looping = scripted_llm([fake_response(("fetch", {"url": "u"})), fake_response(("fetch", {"url": "u"}))])
    run_agent(run_id="r1", agent="a_1", control=FakeControl(["run"]), llm=looping, model="m",
              executor=FakeExecutor(), max_steps=2, tracer=tracer)
    run_agent(run_id="r1", agent="a_1", control=FakeControl(["stop"]), llm=scripted_llm([]), model="m",
              executor=FakeExecutor(), tracer=tracer)
    assert tracer.stops == ["finished", "max_steps", "stopped"]
