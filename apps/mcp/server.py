from fastapi import FastAPI

from src.interfaces.mcp.tools import mcp

app = FastAPI(
    title="Requirement Agent MCP Adapter",
    version="0.1.0",
    description="MCP HTTP 适配器入口，暴露统一的能力工具。",
)

app.mount("/mcp", mcp.streamable_http_app())


@app.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}
