import time

import pytest
from fastapi.testclient import TestClient

from arena.control import create_app
from arena.state import ArenaState


class FakeLauncher:
    def __init__(self, state, violate_agent=None):
        self.state = state
        self.violate_agent = violate_agent
        self.calls = []

    def run(self, run_id, agents):
        self.calls.append((run_id, list(agents)))
        if self.violate_agent:
            self.state.log_request(run_id=run_id, agent=self.violate_agent, step=1, tool="notes_write",
                                   service="notes", method="POST", path="/notes", args={"body": "x"})
        return 0


@pytest.fixture
def state(tmp_path):
    return ArenaState(str(tmp_path / "truth.duckdb"))


def client(state, launcher=None):
    return TestClient(create_app(state, launcher or FakeLauncher(state), settle_seconds=0))


def wait_done(c, job_id):
    for _ in range(100):
        body = c.get(f"/confirm/{job_id}").json()
        if body["status"] == "done":
            return body
        time.sleep(0.05)
    raise AssertionError("confirm job never finished")


def test_create_run_and_agent_config(state):
    c = client(state)
    out = c.post("/runs", json={"scenario": "peer_tip_dev", "seed": 3,
                                "agents": ["research_agent_1", "research_agent_2"]}).json()
    run_id = out["run_id"]
    cfg = c.get(f"/runs/{run_id}/agent-config", params={"agent": "research_agent_2"}).json()
    assert cfg["role"] == "research_agent" and cfg["question_id"] == "q2"
    assert "notes_read" in cfg["exposed_tools"] and "lookup_cache" not in cfg["exposed_tools"]
    assert cfg["canary"] == out["canaries"]["research_agent_2"]
    assert "agent: research_agent" in cfg["policy"]
    assert len(state.notes_for(run_id)) == 3


def test_flag_lifecycle(state):
    c = client(state)
    run_id = c.post("/runs", json={"scenario": "clean_dev", "agents": ["research_agent_1"]}).json()["run_id"]
    flag = lambda: c.get(f"/runs/{run_id}/flag", params={"agent": "research_agent_1"}).json()["flag"]
    assert flag() == "run"
    c.post(f"/runs/{run_id}/pause", json={"agent": "research_agent_1"})
    assert flag() == "pause"
    c.post(f"/runs/{run_id}/resume", json={"agent": "research_agent_1"})
    assert flag() == "run"
    c.post(f"/runs/{run_id}/stop", json={"agent": "research_agent_1"})
    c.post(f"/runs/{run_id}/resume", json={"agent": "research_agent_1"})
    assert flag() == "stop"


def test_pause_unknown_run_is_404(state):
    assert client(state).post("/runs/nope/pause", json={"agent": "a_1"}).status_code == 404


def test_confirm_clean(state):
    launcher = FakeLauncher(state)
    c = client(state, launcher)
    run_id = c.post("/runs", json={"scenario": "planted_dev", "agents": ["research_agent_1"]}).json()["run_id"]
    started = c.post(f"/runs/{run_id}/confirm", json={"agent": "research_agent_1"}).json()
    assert len(started["rerun_ids"]) == 4
    body = wait_done(c, started["job_id"])
    assert body["rerun_clean"] is True and body["control_clean"] is True
    assert len(launcher.calls) == 4


def test_confirm_detects_repeat_violation(state):
    c = client(state, FakeLauncher(state, violate_agent="research_agent_1"))
    run_id = c.post("/runs", json={"scenario": "planted_dev", "agents": ["research_agent_1"]}).json()["run_id"]
    job_id = c.post(f"/runs/{run_id}/confirm", json={"agent": "research_agent_1"}).json()["job_id"]
    body = wait_done(c, job_id)
    assert body["rerun_clean"] is False and body["control_clean"] is False
