import json
import subprocess
from types import SimpleNamespace

import pytest

from common.config import REPO_ROOT
from watchdog.actions import memory as memory_mod
from watchdog.actions.memory import BrainMemory, GrepMemory, filter_hits, items_from_result

MISSING = "definitely-not-a-real-binary"


def test_filter_hits_keeps_only_incident_material():
    items = [{"path": "docs/incidents/abc.md", "text": "incident"},
             {"path": "incidents.C1.abc", "text": "fact"},
             {"path": "docs/bladerunner-plan.md", "text": "planted scenario details"},
             {"source": "session:claude", "text": "transcript"}]
    assert [i["text"] for i in filter_hits(items)] == ["incident", "fact"]


def test_filter_hits_keeps_facts_remembered_under_project_incidents():
    items = [{"path": "project.incidents.c1", "text": "remembered"},
             {"path": "project.architecture.notes", "text": "unrelated fact"},
             {"path": "project.incidentsx.c1", "text": "lookalike"}]
    assert [i["text"] for i in filter_hits(items)] == ["remembered"]


LEAKS = [
    {"path": "docs/superpowers/plans/x.md", "source": "docs/incidents/y"},
    {"path": "docs/incidents/../bladerunner-plan.md"},
    {"path": "/tmp/other/docs/incidents/a.md"},
    {"path": "https://evil/docs/incidents/"},
    {"id": "incidents.x", "path": "docs/plan.md"},
    {"path": {"file": "docs/bladerunner-plan.md", "note": "docs/incidents/"}},
    {"path": "project.incidentsx.c1"},
    {"path": "project.incidents."},
    {"path": "incidents."},
    {"path": "docs/incidents/.md"},
    {"path": "docs/incidents/a.txt"},
    {"path": "docs/incidents/a/../../plan.md"},
    {"path": 5},
    {"text": "no ref"},
]


@pytest.mark.parametrize("item", LEAKS)
def test_filter_hits_drops_leaks(item):
    assert filter_hits([item]) == []


def test_filter_hits_normalises_refs():
    items = [{"path": f"{REPO_ROOT}/docs/incidents/a.md"}, {"path": "./docs/incidents/b.md"},
             {"uri": "file://" + str(REPO_ROOT).replace("\\", "/") + "/docs/incidents/c.md"},
             {"file": "docs\\incidents\\d.md"}, {"path": 3, "id": "incidents.C1.abc"}]
    assert filter_hits(items) == items


def test_items_from_result_parses_json_and_text():
    result = SimpleNamespace(content=[SimpleNamespace(text=json.dumps({"results": [{"path": "docs/incidents/a.md"}]})),
                                      SimpleNamespace(text="plain text")])
    assert items_from_result(result) == [{"path": "docs/incidents/a.md"}, {"text": "plain text"}]


def test_grep_memory_ranks_by_overlap(tmp_path):
    (tmp_path / "a.md").write_text("notes_write after planted docs page, fixed by blocking notes tools")
    (tmp_path / "b.md").write_text("canary leaked to notes")
    hits = GrepMemory(tmp_path).search("planted docs notes_write")
    assert hits == ["notes_write after planted docs page, fixed by blocking notes tools"]
    assert GrepMemory(tmp_path / "missing").search("x") == []


def test_brain_falls_back_to_grep_when_mcp_fails(tmp_path):
    incidents = tmp_path / "docs" / "incidents"
    incidents.mkdir(parents=True)
    (incidents / "c1.md").write_text("C1 notes write incident")
    mem = BrainMemory(repo=tmp_path, exe=MISSING)
    assert mem.search("notes write") == ["C1 notes write incident"]


def test_brain_search_uses_filtered_mcp_hits(tmp_path, monkeypatch):
    result = SimpleNamespace(content=[SimpleNamespace(text=json.dumps({"results": [
        {"path": "docs/incidents/c1.md", "text": "kept"},
        {"path": "docs/bladerunner-plan.md", "text": "dropped"}]}))])

    async def fake_call(self, calls):
        assert calls == [("brain_query", {"query": "q", "limit": 50})]
        return [result]

    monkeypatch.setattr(BrainMemory, "_call", fake_call)
    assert BrainMemory(repo=tmp_path).search("q") == ["kept"]


def test_brain_search_query_limit_is_configurable(tmp_path, monkeypatch):
    seen = {}

    async def fake_call(self, calls):
        seen["calls"] = calls
        return [SimpleNamespace(content=[])]

    monkeypatch.setattr(BrainMemory, "_call", fake_call)
    BrainMemory(repo=tmp_path, query_limit=7).search("q")
    assert seen["calls"] == [("brain_query", {"query": "q", "limit": 7})]


def test_brain_search_dedupes_before_slicing(tmp_path, monkeypatch):
    result = SimpleNamespace(content=[SimpleNamespace(text=json.dumps({"results": [
        {"path": "docs/incidents/a.md", "text": "same"}, {"path": "incidents.a", "text": "same"},
        {"path": "docs/incidents/b.md", "text": "other"}]}))])

    async def fake_call(self, calls):
        return [result]

    monkeypatch.setattr(BrainMemory, "_call", fake_call)
    assert BrainMemory(repo=tmp_path).search("q", k=2) == ["same", "other"]


def test_grep_memory_skips_unreadable_files_and_non_directories(tmp_path):
    (tmp_path / "bad.md").write_bytes(b"notes \xff\xfe write")
    assert GrepMemory(tmp_path).search("notes write") == ["notes \ufffd\ufffd write"]
    (tmp_path / "bad.md").unlink()
    (tmp_path / "dir.md").mkdir()
    assert GrepMemory(tmp_path).search("notes") == []
    afile = tmp_path / "file"
    afile.write_text("notes")
    assert GrepMemory(afile).search("notes") == []


def test_brain_search_never_raises_when_fallback_fails(tmp_path, monkeypatch):
    def boom(self, query, k=5):
        raise RuntimeError("grep down")

    monkeypatch.setattr(GrepMemory, "search", boom)
    assert BrainMemory(repo=tmp_path, exe=MISSING).search("q") == []


def test_brain_search_with_incidents_path_being_a_file(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "incidents").write_text("x")
    assert BrainMemory(repo=tmp_path, exe=MISSING).search("q") == []


@pytest.mark.parametrize("case_id", ["../../x", "/etc/foo", "", "a b", "a" * 65, "a.b"])
def test_remember_rejects_bad_case_id(tmp_path, monkeypatch, case_id):
    monkeypatch.setattr(memory_mod.subprocess, "run", lambda *a, **k: pytest.fail("cli ran"))
    assert BrainMemory(repo=tmp_path).remember(case_id, "C1", "x") is False
    assert not (tmp_path / "docs").exists()


@pytest.mark.parametrize("category", ["", "C.1", "../x", "a" * 17, "a b"])
def test_remember_rejects_bad_category(tmp_path, monkeypatch, category):
    monkeypatch.setattr(memory_mod.subprocess, "run", lambda *a, **k: pytest.fail("cli ran"))
    assert BrainMemory(repo=tmp_path).remember("c1", category, "x") is False
    assert not (tmp_path / "docs").exists()


def test_remember_returns_false_when_incidents_dir_is_a_file(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "incidents").write_text("x")
    assert BrainMemory(repo=tmp_path, exe=MISSING).remember("c1", "C1", "x") is False


def test_remember_passes_safe_subprocess_options(tmp_path, monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 1, "", "")

    monkeypatch.setattr(memory_mod.subprocess, "run", fake_run)
    BrainMemory(repo=tmp_path).remember("c1", "C1", "x")
    assert seen["stdin"] == subprocess.DEVNULL
    assert seen["encoding"] == "utf-8" and seen["errors"] == "replace"


def test_remember_keeps_an_existing_committed_note(tmp_path):
    incidents = tmp_path / "docs" / "incidents"
    incidents.mkdir(parents=True)
    (incidents / "c9.md").write_text("patch note from the fix commit")
    BrainMemory(repo=tmp_path, exe=MISSING).remember("c9", "C1", "confirmed clean")
    assert (incidents / "c9.md").read_text() == "patch note from the fix commit"


def test_brain_remember_writes_note_even_if_cli_missing(tmp_path):
    mem = BrainMemory(repo=tmp_path, exe=MISSING)
    assert mem.remember("c9", "C1", "blocked notes tools") is False
    assert "blocked notes tools" in (tmp_path / "docs" / "incidents" / "c9.md").read_text()


def test_remember_invokes_cli_with_path_and_returns_true(tmp_path, monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"], seen["kwargs"] = argv, kwargs
        return subprocess.CompletedProcess(argv, 0, "", "")

    async def fake_call(self, calls):
        seen["refresh"] = calls
        return []

    monkeypatch.setattr(memory_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(BrainMemory, "_call", fake_call)
    mem = BrainMemory(repo=tmp_path, exe="entire")
    assert mem.remember("c1", "C1", "blocked notes tools") is True
    assert seen["argv"] == ["entire", "brain", "remember", "Incident c1 (C1): blocked notes tools",
                            "--path", "project.incidents.c1", "--json"]
    assert seen["kwargs"]["cwd"] == tmp_path
    assert seen["kwargs"]["timeout"] == 60
    assert seen["refresh"] == [("brain_refresh", {})]


def test_remember_returns_false_on_nonzero_exit(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_mod.subprocess, "run",
                        lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "boom"))
    assert BrainMemory(repo=tmp_path).remember("c2", "C2", "x") is False
    assert (tmp_path / "docs" / "incidents" / "c2.md").exists()


def test_remember_timeout_returns_false_and_keeps_note(tmp_path, monkeypatch):
    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 60)

    monkeypatch.setattr(memory_mod.subprocess, "run", fake_run)
    assert BrainMemory(repo=tmp_path).remember("c3", "C1", "slow") is False
    assert "slow" in (tmp_path / "docs" / "incidents" / "c3.md").read_text()


def test_refresh_failure_does_not_change_result(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_mod.subprocess, "run",
                        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "", ""))

    async def broken_call(self, calls):
        raise RuntimeError("mcp down")

    monkeypatch.setattr(BrainMemory, "_call", broken_call)
    assert BrainMemory(repo=tmp_path).remember("c4", "C1", "x") is True


def test_read_only_never_writes(tmp_path):
    mem = BrainMemory(repo=tmp_path, exe=MISSING, read_only=True)
    assert mem.remember("c9", "C1", "x") is False
    assert not (tmp_path / "docs" / "incidents" / "c9.md").exists()
