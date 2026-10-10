import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from common.config import REPO_ROOT, tool_env


async def main() -> None:
    params = StdioServerParameters(command="entire", args=["brain", "mcp"], cwd=str(REPO_ROOT), env=tool_env())
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            for t in tools.tools:
                print(t.name, t.input_schema)
            status = await s.call_tool("brain_status", {})
            print("status:", [getattr(b, "text", "") for b in status.content])


anyio.run(main)
