from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import anyio

from common.config import REPO_ROOT, tool_env

NOTE_PREFIX = "docs/incidents/"
FACT_PREFIXES = ("incidents.", "project.incidents.")
_REF_KEYS = ("path", "file", "uri", "source", "id")
_ROOT = str(REPO_ROOT).replace("\\", "/").rstrip("/") + "/"
_MCP_TIMEOUT_S = 20
_CLI_TIMEOUT_S = 60


def filter_hits(items: list[dict]) -> list[dict]:
    return [i for i in items if _is_incident_ref(_canonical_ref(i))]


def _canonical_ref(item: dict) -> str:
    ref = next((v for k in _REF_KEYS if isinstance(v := item.get(k), str) and v), "")
    ref = ref.replace("\\", "/").removeprefix("file://").removeprefix(_ROOT).removeprefix("./")
    return "" if ".." in ref.split("/") else ref


def _is_incident_ref(ref: str) -> bool:
    if ref.startswith(NOTE_PREFIX):
        return ref.endswith(".md") and len(ref.rsplit("/", 1)[-1]) > 3
    return any(ref.startswith(p) and len(ref) > len(p) for p in FACT_PREFIXES)


def items_from_result(result) -> list[dict]:
    out: list[dict] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", "") or ""
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            out.append({"text": text})
            continue
        if isinstance(data, dict):
            data = data.get("results") or data.get("items") or [data]
        if isinstance(data, list):
            out.extend(d if isinstance(d, dict) else {"text": str(d)} for d in data)
    return out


def _item_text(item: dict) -> str:
    return str(item.get("text") or item.get("content") or item.get("fact") or json.dumps(item))[:1500]


class GrepMemory:
    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def search(self, query: str, k: int = 5) -> list[str]:
        if not self.directory.is_dir():
            return []
        words = {w.lower() for w in re.findall(r"\w+", query) if len(w) > 2}
        scored = []
        for path in sorted(self.directory.glob("*.md")):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            score = sum(text.lower().count(w) for w in words)
            if score:
                scored.append((score, text[:1500]))
        return [t for _, t in sorted(scored, key=lambda x: -x[0])[:k]]

    def remember(self, case_id: str, category: str, text: str) -> bool:
        return False


class BrainMemory:
    def __init__(self, repo: Path = REPO_ROOT, exe: str = "entire", query_tool: str = "brain_query",
                 query_arg: str = "query", refresh_tool: str = "brain_refresh", read_only: bool = False,
                 query_limit: int = 50):
        self.repo = Path(repo)
        self.exe = exe
        self.query_tool, self.query_arg = query_tool, query_arg
        self.refresh_tool = refresh_tool
        self.read_only = read_only
        self.query_limit = query_limit
        self.incidents_dir = self.repo / "docs" / "incidents"
        self.fallback = GrepMemory(self.incidents_dir)

    async def _call(self, calls: list[tuple[str, dict]]) -> list:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(command=self.exe, args=["brain", "mcp"], cwd=str(self.repo), env=tool_env())
        results = []
        with anyio.fail_after(_MCP_TIMEOUT_S):
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    for tool, args in calls:
                        results.append(await session.call_tool(tool, args))
        return results

    def search(self, query: str, k: int = 5) -> list[str]:
        try:
            results = anyio.run(self._call, [(self.query_tool, {self.query_arg: query, "limit": self.query_limit})])
            texts = list(dict.fromkeys(_item_text(i) for i in filter_hits(items_from_result(results[0]))))[:k]
        except Exception:
            texts = []
        try:
            return texts or self.fallback.search(query, k)
        except Exception:
            return []

    def remember(self, case_id: str, category: str, text: str) -> bool:
        if self.read_only or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", case_id) \
                or not re.fullmatch(r"[A-Za-z0-9_]{1,16}", category):
            return False
        try:
            self.incidents_dir.mkdir(parents=True, exist_ok=True)
            note = self.incidents_dir / f"{case_id}.md"
            if not note.exists():
                note.write_text(f"# Incident {case_id} ({category})\n\n{text}\n", encoding="utf-8")
            fact = f"Incident {case_id} ({category}): {text}"
            proc = subprocess.run([self.exe, "brain", "remember", fact, "--path",
                                   f"project.incidents.{category.lower()}", "--json"],
                                  cwd=self.repo, env=tool_env(), capture_output=True, text=True,
                                  stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace",
                                  timeout=_CLI_TIMEOUT_S)
            if proc.returncode != 0:
                return False
            try:
                anyio.run(self._call, [(self.refresh_tool, {})])
            except Exception:
                pass
            return True
        except Exception:
            return False
