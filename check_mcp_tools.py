import asyncio
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main():
    url = os.getenv("MCP_URL", "http://localhost:8000/mcp")
    token = os.getenv("MCP_AUTH_TOKEN", "").strip()
    if not token:
        raise RuntimeError("MCP_AUTH_TOKEN must be configured")
    print(f"正在连接 MCP 服务: {url}")
    try:
        async with streamablehttp_client(
            url,
            headers={"Authorization": f"Bearer {token}"},
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                print("当前 MCP 实际工具列表：")
                if not tools.tools:
                    print("- 无工具暴露")
                    return
                for tool in tools.tools:
                    print(f"- {tool.name}")
                    if getattr(tool, "description", None):
                        print(f"  描述: {tool.description}")
    except Exception as e:
        print(f"连接失败: {e}")
        print("请确认以下条件：")
        print("1. 项目已启动 MCP 服务")
        print("2. 服务监听在 http://localhost:8000/mcp")
        print("3. 不要同时启动两套 MCP server")
        print("4. 仅保留 apps/mcp/server.py 作为正式入口")


if __name__ == "__main__":
    asyncio.run(main())
