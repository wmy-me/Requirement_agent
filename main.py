from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.interfaces.http.routes import router

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(
    title="Requirement Agent API",
    version="0.1.0",
    description="渠道接入、查询和审批的 API 服务入口。",
)
app.include_router(router)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ui", include_in_schema=False)
async def ui_page() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


__all__ = ["app"]


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8888, reload=True)
