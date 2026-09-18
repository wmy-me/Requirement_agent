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
import json

from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from requirement_agent.config.settings import settings
from requirement_agent.infrastructure.worker.consumer import OutboxConsumer
from requirement_agent.api.dependencies import requirement_analysis_task
from requirement_agent.api.auth import has_scope, required_scope, resolve_role
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

#: 新工作台的页面白名单（`static/app/<name>.html`）。**不从 URL 拼路径** ——
#: 见 `workbench_page` 的说明。
_WORKBENCH_PAGES = frozenset(
    {"index", "requirements", "reviews", "versions", "analysis", "knowledge", "intake", "ops"}
)


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
        # ⚠️ **必须先判 `is_alive()`** —— `Thread.join()` 在**没启动过**的线程上会抛
        # `RuntimeError: cannot join thread before it is started`。而
        # `OUTBOX_CONSUMER_ENABLED=false` 时正是「不启动」，于是**关进程就报错**。
        # 实测踩到；而这恰好是 B5 推荐的生产形态（API 侧关掉内嵌消费）。
        # is_alive() 为假有两种情况——没启动过、或已经跑完——两种都不需要 join。
        if thread.is_alive():
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

    # 鉴权（B1）：**定义在最前面 = 最内层**，这样 401/403 也会被日志中间件记到、
    # 也会带上安全响应头。豁免路径见 `api/auth.py` 的 EXEMPT_*。
    @app.middleware("http")
    async def enforce_api_auth(request: Request, call_next):
        scope = required_scope(request.method, request.url.path)
        if scope is None:  # 豁免：飞书 webhook / 探活 / 静态资源 / OpenAPI 文档
            return await call_next(request)

        # ⚠️ **复用外层日志中间件生成的 request_id**，不要自己再生成一个 ——
        # 否则 401 响应体里的 id 与 `X-Request-ID` 响应头（以及两条日志）会对不上，
        # 那个 id 就失去了「串起整条链路」的意义。实测被测试抓到过。
        request_id = getattr(request.state, "request_id", None) or uuid4().hex[:8]
        role = resolve_role(request.headers.get("Authorization"))

        if role is None:
            # 401 = 没带凭证 / 凭证无效 / **服务端根本没配 token**（默认拒绝）
            logger.warning(
                "event=auth_unauthorized method=%s path=%s request_id=%s",
                request.method, request.url.path, request_id,
            )
            return JSONResponse(
                status_code=401,
                content={
                    "detail": "缺少或无效的 API token（Authorization: Bearer <token>）",
                    "request_id": request_id,
                    "required_scope": scope,
                },
                headers={"X-Request-ID": request_id, "WWW-Authenticate": "Bearer"},
            )

        if not has_scope(role, scope):
            # 403 = 身份有效但权限不够。**与 401 分开**：前者该换 token，后者该换角色。
            logger.warning(
                "event=auth_forbidden method=%s path=%s role=%s scope=%s request_id=%s",
                request.method, request.url.path, role, scope, request_id,
            )
            return JSONResponse(
                status_code=403,
                content={
                    "detail": f"角色 {role} 没有 {scope} 权限",
                    "request_id": request_id,
                    "required_scope": scope,
                    "role": role,
                },
                headers={"X-Request-ID": request_id},
            )

        # 供下游使用（如按角色决定 actor_id）；本批次不改现有 actor 语义
        request.state.auth_role = role
        return await call_next(request)

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
        # 放进 state 供内层中间件（鉴权）复用 —— 整个请求只应有一个 request_id
        request.state.request_id = request_id
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

    def _render_page(path) -> HTMLResponse:
        """读一个页面 HTML，**并把 API token 直接注入**。

        ## 为什么注入而不是让前端自己拿

        这是**公司内部共用的一套工作台**：大家看同一个需求池、用同一套模型，
        没有「每个用户一个身份」这回事。token 是**服务入口凭证**，不是个人凭证 ——
        让每个人去填纯是摩擦，没有任何防护收益。

        ## 为什么注入进 HTML，而不是发一个 `ui-config.js`

        那多了一次请求，也就多了一个会失败的地方（漏加载 / 404 / 顺序不对 →
        前端拿不到 token）。注入进 HTML 是**一次响应里同时拿到页面和 token**。

        ## 每个页面都要注入

        新工作台是**多个独立 HTML**（`static/app/*.html`），每个都是完整文档、
        各自发请求 —— 所以注入必须逐页做。漏掉一页，那一页就全是 401。
        """
        html = path.read_text(encoding="utf-8")
        snippet = (
            "<script>/* 服务端注入，勿手改 */"
            f"window.RA_UI_TOKEN = {json.dumps(settings.frontend_token())};</script>"
        )
        marker = "<!-- RA_UI_TOKEN_INJECT -->"
        if marker in html:
            return HTMLResponse(html.replace(marker, snippet))
        logger.warning("event=ui_token_marker_missing path=%s", path)
        return HTMLResponse(html.replace("</head>", snippet + "</head>", 1))

    @app.get("/ui", include_in_schema=False)
    async def ui_page() -> HTMLResponse:
        """旧工作台（对话为中心）。**保留不动** —— 新工作台逐页替换它。"""
        return _render_page(STATIC_DIR / "index.html")

    @app.get("/app", include_in_schema=False)
    @app.get("/app/{page}", include_in_schema=False)
    async def workbench_page(page: str = "index") -> HTMLResponse:
        """新工作台的一个页面（`static/app/<page>.html`）。

        ⚠️ **页名走白名单**，不做路径拼接 —— `page` 直接来自 URL，拼进路径就是
        目录穿越（`../../.env`）。白名单同时让「有哪些页面」这件事在代码里可见。
        """
        if page not in _WORKBENCH_PAGES:
            raise HTTPException(status_code=404, detail=f"未知页面：{page}")
        return _render_page(STATIC_DIR / "app" / f"{page}.html")

    return app


app = create_app()

__all__ = ["app", "create_app"]