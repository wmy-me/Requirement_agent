import uvicorn
from fastapi import FastAPI

from src.interfaces.http.routes import router

app = FastAPI(
    title="Requirement Agent API",
    version="0.1.0",
    description="渠道接入、查询和审批的 API 服务入口。",
)
app.include_router(router)


@app.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


__all__ = ["app"]


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
