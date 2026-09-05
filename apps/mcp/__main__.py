import uvicorn

from src.config.settings import settings


if __name__ == "__main__":
    uvicorn.run("apps.mcp.server:app", host="0.0.0.0", port=settings.mcp_port)
