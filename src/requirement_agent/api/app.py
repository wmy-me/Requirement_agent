"""requirement_agent.api.app —— 统一 FastAPI 应用入口（目标命名空间）。

当前行为与根 `main.py` 保持一致（应用名/版本/描述/路由 /health /ui /static、安全响应头、
lifespan outbox 消费循环）。根 `main.py` 为薄包装转发到 `app = create_app()`，**不产生第二个 FastAPI app、不重复注册路由**；
后台 Worker 入口在 `requirement_agent.workers.tasks:app`。
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from requirement_agent.config.settings import settings
from requirement_agent.infrastructure.worker.consumer import OutboxConsumer
from requirement_agent.api.dependencies import requirement_analysis_task
from requirement_agent.api.router import router

logger = logging.getLogger(__name__)

# 项目根目录：本文件位于 <root>/src/requirement_agent/api/app.py
PROJECT_ROOT = Path(__file__).resolve().parents[3]
STATIC_DIR = PROJECT_ROOT / "static"

# 不记请求日志的路径：静态资源、前端页面与探活。
# 前端每 30 秒轮询两个 health 端点（`app.js` 的 loadStatus），不跳过会把日志刷满，
# 真正出问题时反而淹没在噪声里。
_LOG_SKIP_PREFIXES = ("/static", "/api/v1/health", "/favicon.ico")
_LOG_SKIP_PATHS = {"/", "/ui", "/health", "/docs", "/redoc", "/openapi.json"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：随 API 进程启动后台 outbox 消费循环，退出时优雅停止。

    与根 main.py 完全一致：worker 服务不常驻部署，由 API 进程承担 embedding /
    文档分片的 outbox 消费（`FOR UPDATE SKIP LOCKED` 保证多实例并发安全）。
    """
    # 入口此前没有任何日志配置：模块级 logger 的记录会因 root 无 handler 而被
    # lastResort 丢弃（只放 WARNING 及以上到 stderr，且无时间戳）。这里给 root
    # 装一次 handler。若外部已通过 --log-config 配好，basicConfig 是空操作。
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    # 显式注入分析任务：不注入的话 requirement_analysis 事件不会被消费，
    # 渠道接入的需求会永远停在 received（consumer 会为此打告警日志）。
    consumer = OutboxConsumer(analysis_task=requirement_analysis_task)
    # 挂到 app.state 供 /api/v1/ops/outbox 读运行统计。它由本 lifespan 启停、
    # 生命周期与 app 一致，不适合放进 dependencies.py 的模块级单例。
    app.state.outbox_consumer = consumer
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

    # 请求级日志：此前全仓没有请求耗时也没有 request id，出问题只能靠猜。
    # 本中间件定义在 add_security_headers **之后** —— Starlette 的中间件是 LIFO，
    # 后定义的更靠外，这样算出的 duration_ms 才覆盖到安全头那一层。
    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        # 沿用入站 X-Request-ID（便于与网关/前端串联），没有则生成一个
        request_id = request.headers.get("X-Request-ID") or uuid4().hex[:8]
        started_at = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "event=http_request method=%s path=%s status=exception duration_ms=%.1f request_id=%s",
                request.method,
                request.url.path,
                (time.perf_counter() - started_at) * 1000,
                request_id,
            )
            raise
        duration_ms = (time.perf_counter() - started_at) * 1000
        response.headers["X-Request-ID"] = request_id
        path = request.url.path
        if path not in _LOG_SKIP_PATHS and not path.startswith(_LOG_SKIP_PREFIXES):
            log = logger.warning if response.status_code >= 400 else logger.info
            log(
                "event=http_request method=%s path=%s status=%d duration_ms=%.1f request_id=%s",
                request.method,
                path,
                response.status_code,
                duration_ms,
                request_id,
            )
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