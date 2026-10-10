from common.tracing import NullTracer


def test_null_tracer_decorators_are_identity():
    def f(x):
        return x * 2

    assert NullTracer.tool("fetch")(f)(3) == 6
    assert NullTracer.guardrail("guardrail_block")(f)(2) == 4
    assert NullTracer.workflow("arena-run", session_id="r1")(f)(1) == 2
    NullTracer.set_attribute("k", "v")
    NullTracer.stopped("finished")
