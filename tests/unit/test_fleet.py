import json
import sys

from fleet import cli
from fleet.cli import agent_names, build_agent_cmd, run_agents


def test_agent_names():
    assert agent_names(3) == ["research_agent_1", "research_agent_2", "research_agent_3"]


def test_build_agent_cmd():
    assert build_agent_cmd("r1", "research_agent_2") == [
        sys.executable, "-m", "arena.agent", "--run-id", "r1", "--agent", "research_agent_2"]


def test_run_agents_spawns_one_process_per_agent():
    spawned = []

    class FakeProc:
        def __init__(self, cmd, **kwargs):
            spawned.append(cmd)

        def wait(self, timeout=None):
            return 0

    codes = run_agents("r1", ["a_1", "a_2"], popen=FakeProc)
    assert codes == [0, 0] and len(spawned) == 2 and spawned[1][-1] == "a_2"


def test_main_uses_agents_flag_and_records_one_json_line(tmp_path, monkeypatch):
    launched = []
    monkeypatch.setattr(cli, "run_agents", lambda run_id, agents: launched.append((run_id, agents)) or [0, 0])
    record = tmp_path / "bench_runs.jsonl"
    code = cli.main(["run", "--run-id", "r9", "--scenario", "planted", "--agents", "2", "--record", str(record)])
    assert code == 0 and launched == [("r9", ["research_agent_1", "research_agent_2"])]
    lines = record.read_text(encoding="utf-8").splitlines()
    assert [json.loads(x) for x in lines] == [
        {"run_id": "r9", "scenario": "planted", "agents": ["research_agent_1", "research_agent_2"]}]
