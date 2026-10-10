from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import anyio

from common.config import REPO_ROOT, tool_env

INCIDENT_MARKERS = ("docs/incidents/",)
_REF_KEYS = ("path", "source", "id", "file", "uri")
_MCP_TIMEOUT_S = 20
_CLI_TIMEOUT_S = 60


def filter_hits(items: list[dict]) -> list[dict]:
    keep = []
    for item in items:
        refs = [str(item.get(k, "")) for k in _REF_KEYS]
        if any(m in r for r in refs for m in INCIDENT_MARKERS) or any(r.startswith("incidents.") for r in refs):
            keep.append(item)
    return keep


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
        if not self.directory.exists():
            return []
        words = {w.lower() for w in re.findall(r"\w+", query) if len(w) > 2}
        scored = []
        for path in sorted(self.directory.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            score = sum(text.lower().count(w) for w in words)
            if score:
                scored.append((score, text[:1500]))
        return [t for _, t in sorted(scored, key=lambda x: -x[0])[:k]]

    def remember(self, case_id: str, category: str, text: str) -> bool:
        return False


class BrainMemory:
    def __init__(self, repo: Path = REPO_ROOT, exe: str = "entire", query_tool: str = "brain_query",
                 query_arg: str = "query", refresh_tool: str = "brain_refresh", read_only: bool = False):
        self.repo = Path(repo)
        self.exe = exe
        self.query_tool, self.query_arg = query_tool, query_arg
        self.refresh_tool = refresh_tool
        self.read_only = read_only
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
            results = anyio.run(self._call, [(self.query_tool, {self.query_arg: query})])
        except Exception:
            return self.fallback.search(query, k)
        texts = [_item_text(i) for i in filter_hits(items_from_result(results[0]))][:k]
        return texts or self.fallback.search(query, k)

    def remember(self, case_id: str, category: str, text: str) -> bool:
        if self.read_only:
            return False
        self.incidents_dir.mkdir(parents=True, exist_ok=True)
        (self.incidents_dir / f"{case_id}.md").write_text(f"# Incident {case_id} ({category})\n\n{text}\n",
                                                          encoding="utf-8")
        fact = f"Incident {case_id} ({category}): {text}"
        try:
            proc = subprocess.run([self.exe, "brain", "remember", fact, "--path",
                                   f"project.incidents.{category.lower()}", "--json"],
                                  cwd=self.repo, env=tool_env(), capture_output=True, text=True,
                                  timeout=_CLI_TIMEOUT_S)
        except (OSError, subprocess.TimeoutExpired):
            return False
        if proc.returncode != 0:
            return False
        try:
            anyio.run(self._call, [(self.refresh_tool, {})])
        except Exception:
            pass
        return True
