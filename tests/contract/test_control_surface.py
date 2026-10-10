import time

from fastapi.testclient import TestClient

from arena.control import WATCHDOG_KEYS, create_app
from arena.state import ArenaState


class NoopLauncher:
    def run(self, run_id, agents):
        return 0


def test_watchdog_facing_responses_leak_nothing(tmp_path):
    state = ArenaState(str(tmp_path / "truth.duckdb"))
    c = TestClient(create_app(state, NoopLauncher(), settle_seconds=0))
    run_id = c.post("/runs", json={"scenario": "planted", "agents": ["research_agent_1"]}).json()["run_id"]
    body = {"agent": "research_agent_1"}
    responses = [c.post(f"/runs/{run_id}/{action}", json=body).json() for action in ("pause", "resume")]
    started = c.post(f"/runs/{run_id}/confirm", json=body).json()
    responses.append(started)
    time.sleep(0.2)
    responses.append(c.get(f"/confirm/{started['job_id']}").json())
    responses.append(c.post(f"/runs/{run_id}/stop", json=body).json())
    for r in responses:
        assert set(r) <= WATCHDOG_KEYS, r
        assert "planted" not in str(r) and "scenario" not in str(r)
