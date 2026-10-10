import json
from types import SimpleNamespace

import pytest


def _response(*calls, content=None, usage=None):
    tool_calls = [
        SimpleNamespace(id=f"call_{i}", function=SimpleNamespace(name=name, arguments=json.dumps(args)))
        for i, (name, args) in enumerate(calls)
    ]
    message = SimpleNamespace(content=content, tool_calls=tool_calls or None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


class ScriptedLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append({**kwargs, "messages": [dict(m) for m in kwargs.get("messages", [])]})
        return self.responses.pop(0)


@pytest.fixture
def fake_response():
    return _response


@pytest.fixture
def scripted_llm():
    return ScriptedLLM
