import threading
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.config.settings import settings
from src.infrastructure.worker.consumer import OutboxConsumer
from src.interfaces.http.routes import router

BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：随 API 进程启动后台 outbox 消费循环，退出时优雅停止。

    背景：worker 服务不常驻部署，审核通过入队的 embedding 同步事件长期无人消费，
    导致语义向量不更新。这里由 API 进程统一承担 outbox 消费，保证：
    - 每条待处理事件被持续认领执行（间隔与批次见 settings.outbox_*）；
    - 多实例并发安全由 outbox 的 `FOR UPDATE SKIP LOCKED` 保证。
    """
    consumer = OutboxConsumer()
    thread = threading.Thread(target=consumer.start, name="outbox-consumer", daemon=True)
    if settings.outbox_consumer_enabled:
        thread.start()
    try:
        yield
    finally:
        consumer.stop()
        thread.join(timeout=settings.outbox_poll_interval + 1)


app = FastAPI(
    title="Requirement Agent API",
    version="0.1.0",
    description="渠道接入、查询和审批的 API 服务入口。",
    lifespan=lifespan,
)
app.include_router(router)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'",
    )
    # 前端资源不缓存重验证，避免浏览器沿用旧版 JS（历史会话加载失败通常源于旧缓存）
    path = request.url.path
    if path == "/ui" or path.endswith(".js") or path.endswith(".css"):
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


@app.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ui", include_in_schema=False)
async def ui_page() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


__all__ = ["app"]


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8888, reload=True)
