import os
import shutil
from pathlib import Path

import pytest

from common import config


def test_host_formats_port():
    assert config.host("notes") == "127.0.0.1:8102"
    assert config.host("control") == "127.0.0.1:8100"


def test_env_reads_value(monkeypatch):
    monkeypatch.setenv("BR_TEST_VALUE", "abc")
    assert config.env("BR_TEST_VALUE") == "abc"


def test_env_default_and_missing(monkeypatch):
    monkeypatch.delenv("BR_TEST_MISSING", raising=False)
    assert config.env("BR_TEST_MISSING", "x") == "x"
    with pytest.raises(KeyError):
        config.env("BR_TEST_MISSING")


def test_tool_env_prepends_entire_dirs():
    path = config.tool_env()["PATH"]
    assert path.startswith(str(Path.home() / ".local" / "bin"))
    assert "plugins" in path.split(os.pathsep)[1]


@pytest.mark.skipif(os.name == "nt", reason="posix plugin layout")
def test_tool_env_uses_posix_entire_plugin_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("ENTIRE_PLUGIN_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    plugin_bin = config.tool_env()["PATH"].split(os.pathsep)[1]
    assert plugin_bin == str(Path.home() / ".local" / "share" / "entire" / "plugins" / "bin")
    monkeypatch.setenv("ENTIRE_PLUGIN_DIR", str(tmp_path))
    assert config.tool_env()["PATH"].split(os.pathsep)[1] == str(tmp_path / "bin")


def _git_has_sibling_sh() -> bool:
    git = shutil.which("git")
    if git is None:
        return False
    exe_dir = Path(git).resolve().parent
    return any(
        (root / "bin" / name).exists()
        for root in (exe_dir.parent, exe_dir.parent.parent)
        for name in ("sh.exe", "sh")
    )


@pytest.mark.skipif(not _git_has_sibling_sh(), reason="git install has no sh next to its bin dir")
def test_tool_env_puts_git_sh_on_path(monkeypatch):
    git = shutil.which("git")
    monkeypatch.setenv("PATH", str(Path(git).parent))
    assert shutil.which("sh", path=os.environ["PATH"]) is None
    assert shutil.which("sh", path=config.tool_env()["PATH"]) is not None
