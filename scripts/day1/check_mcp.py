import asyncio
import sys

from common.config import env, load_env

load_env()
import _rest  # noqa: E402


async def mcp_check(tid: str) -> None:
    from mcp import ClientSession
    import httpx2
    from mcp.client.streamable_http import streamable_http_client

    hc = httpx2.AsyncClient(headers={"Authorization": f"Bearer {env('NEATLOGS_INGEST_KEY')}"}, timeout=60)
    async with hc, streamable_http_client("https://ingest.neatlogs.com/mcp", http_client=hc) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            print("mcp tools:", [t.name for t in tools.tools])
            ctx_tool = next(t for t in tools.tools if t.name == "get_trace_context")
            print("get_trace_context schema:", ctx_tool.input_schema)
            key = ctx_tool.input_schema.get("required", ["trace_id"])[0]
            res = await s.call_tool("get_trace_context", {key: tid})
            text = " ".join(getattr(b, "text", "") for b in res.content)
            _rest.save("mcp_trace_context", {"text": text})
            print("mcp context has target_host:", "target_host" in text)


if __name__ == "__main__":
    asyncio.run(mcp_check(sys.argv[1]))
