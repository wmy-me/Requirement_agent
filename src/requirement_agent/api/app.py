"""requirement_agent.api.app —— 统一 FastAPI 应用入口（目标命名空间）。

当前行为与根 `main.py` 保持一致（应用名/版本/描述/路由 /health /ui /static、安全响应头、
lifespan outbox 消费循环）。旧入口 main.py / apps/api/main.py / apps/api/__main__.py
均改为薄包装转发到 `app = create_app()`，**不产生第二个 FastAPI app、不重复注册路由**。
"""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.config.settings import settings
from src.infrastructure.worker.consumer import OutboxConsumer
from src.interfaces.http.routes import router

# 项目根目录：本文件位于 <root>/src/requirement_agent/api/app.py
PROJECT_ROOT = Path(__file__).resolve().parents[3]
STATIC_DIR = PROJECT_ROOT / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：随 API 进程启动后台 outbox 消费循环，退出时优雅停止。

    与根 main.py 完全一致：worker 服务不常驻部署，由 API 进程承担 embedding /
    文档分片的 outbox 消费（`FOR UPDATE SKIP LOCKED` 保证多实例并发安全）。
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


def create_app() -> FastAPI:
    """构造统一 FastAPI 应用（工厂函数）。"""
    app = FastAPI(
        title="Requirement Agent API",
        version="0.1.0",
        description="渠道接入、查询和审批的 API 服务入口。",
        lifespan=lifespan,
    )
    app.include_router(router)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

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
        # 前端资源不缓存重验证，避免浏览器沿用旧版 JS
        path = request.url.path
        if path == "/ui" or path.endswith(".js") or path.endswith(".css"):
            response.headers.setdefault("Cache-Control", "no-cache")
        return response

    @app.get("/health")
    async def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ui", include_in_schema=False)
    async def ui_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()

__all__ = ["app", "create_app"]