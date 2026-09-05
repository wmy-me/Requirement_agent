import asyncio
import json
import os

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


for key in [
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "HTTPS_PROXY",
    "httpss_proxy",
]:
    os.environ.pop(key, None)


async def main() -> None:
    url = os.getenv("MCP_URL", "http://localhost:8000/mcp")
    token = os.getenv("MCP_AUTH_TOKEN", "").strip()
    if not token:
        raise RuntimeError("MCP_AUTH_TOKEN must be configured")
    print(f"连接 MCP 服务: {url}")

    def client_factory(**kwargs):
        return httpx.AsyncClient(trust_env=False, **kwargs)

    async with streamablehttp_client(
        url,
        headers={"Authorization": f"Bearer {token}"},
        httpx_client_factory=client_factory,
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print("\n已发现工具：")
            for tool in tools.tools:
                print(f"- {tool.name}: {tool.description}")

            print("\n调用 search_requirements ...")
            result = await session.call_tool(
                "search_requirements",
                {"query": "登录", "limit": 5},
            )

            print("\n调用结果：")
            print(json.dumps(result.model_dump() if hasattr(result, "model_dump") else result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
