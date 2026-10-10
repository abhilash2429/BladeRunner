import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def imported_modules(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def test_watchdog_and_scorer_never_touch_arena_or_truth():
    files = list((ROOT / "watchdog").rglob("*.py")) + [ROOT / "bench" / "score.py"]
    for f in (p for p in files if p.exists()):
        for mod in imported_modules(f):
            assert mod != "arena" and not mod.startswith("arena."), f"{f} imports {mod}"
        assert "truth.duckdb" not in f.read_text(encoding="utf-8"), f"{f} mentions truth.duckdb"
