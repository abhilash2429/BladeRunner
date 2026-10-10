from __future__ import annotations

from typing import Callable

from common.config import env


def init(workflow_name: str, tags: list[str]) -> None:
    import neatlogs

    neatlogs.init(api_key=env("NEATLOGS_INGEST_KEY"), workflow_name=workflow_name, tags=tags,
                  instrumentations=["openai"], flush_interval=1.0)


def wrap(client):
    import neatlogs

    return neatlogs.wrap(client)


def workflow(name: str, session_id: str) -> Callable:
    import neatlogs

    return neatlogs.span(kind="WORKFLOW", name=name, session_id=session_id)


def tool(name: str) -> Callable:
    import neatlogs

    return neatlogs.span(kind="TOOL", name=name, tool_name=name)


def guardrail(name: str) -> Callable:
    import neatlogs

    return neatlogs.span(kind="GUARDRAIL", name=name)


def set_attribute(key: str, value) -> None:
    try:
        from opentelemetry import trace
    except ImportError:
        return
    trace.get_current_span().set_attribute(key, value)


def stopped(reason: str) -> None:
    import neatlogs

    @neatlogs.span(kind="TOOL", name="run_stopped", tool_name="run_stopped")
    def run_stopped(reason: str) -> str:
        return reason

    run_stopped(reason=reason)


def shutdown() -> None:
    import neatlogs

    neatlogs.flush()
    neatlogs.shutdown()


class NullTracer:
    @staticmethod
    def workflow(name: str, session_id: str) -> Callable:
        return lambda f: f

    @staticmethod
    def tool(name: str) -> Callable:
        return lambda f: f

    @staticmethod
    def guardrail(name: str) -> Callable:
        return lambda f: f

    @staticmethod
    def set_attribute(key: str, value) -> None:
        return None

    @staticmethod
    def stopped(reason: str) -> None:
        return None
