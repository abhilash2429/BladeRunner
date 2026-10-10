from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

SCENARIO_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Scenario:
    id: str
    split: str
    tier: int
    questions: tuple[str, ...]
    docs_page: str
    data_api: str
    seed_notes: str | None
    expose_unlisted: bool
    expected: tuple[str, ...]


def _from_file(path: Path) -> Scenario:
    d = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Scenario(id=d["id"], split=d["split"], tier=int(d["tier"]), questions=tuple(d["questions"]),
                    docs_page=d["docs_page"], data_api=d["data_api"], seed_notes=d.get("seed_notes"),
                    expose_unlisted=bool(d.get("expose_unlisted", False)), expected=tuple(d.get("expected") or ()))


def load_scenario(sid: str) -> Scenario:
    return _from_file(SCENARIO_DIR / f"{sid}.yaml")


def list_scenarios(split: str | None = None) -> list[Scenario]:
    items = [_from_file(p) for p in sorted(SCENARIO_DIR.glob("*.yaml"))]
    return [s for s in items if split is None or s.split == split]


def clean_scenario_for(sc: Scenario) -> str:
    return "clean_dev" if sc.split == "dev" else "clean"
