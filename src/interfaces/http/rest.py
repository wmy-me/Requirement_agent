"""REST 域聚合器（结构重组后）。

所有 REST 路由已迁至 `src.requirement_agent.api.routes.*`；本文件仅按**原注册顺序**
include 各子 router，并转发旧函数名以保持 `src.interfaces.http.rest.<fn>` 兼容
（旧路径与新路径为**同一对象**）。

注意：各子 router 均不设 tags，由本文件的父 router（`tags=["requirements"]`）在 include
时补齐，保持 OpenAPI tags 与原行为一致。
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["requirements"])


# —— 根 / 基础健康 ——（system：`/`、`/health`）
from src.requirement_agent.api.routes.system import healthcheck, root  # noqa: E402,F401
from src.requirement_agent.api.routes.system import router as _system_router  # noqa: E402

router.include_router(_system_router)


# —— 健康检查组 ——（health：`/api/v1/health/db`、`/api/v1/health/llm`）
from src.requirement_agent.api.routes.health import database_health, llm_health  # noqa: E402,F401
from src.requirement_agent.api.routes.health import router as _health_router  # noqa: E402

router.include_router(_health_router)


# —— 需求写入组 ——（requirements_write：列表 / 提交 / multipart 摄取）
from src.requirement_agent.api.routes.requirements_write import (  # noqa: E402,F401
    ingest_requirement,
    list_requirements,
    submit_requirement,
)
from src.requirement_agent.api.routes.requirements_write import router as _requirements_write_router  # noqa: E402

router.include_router(_requirements_write_router)


# —— 相似需求检索组 ——（requirements.search_router：search / features/search）
from src.requirement_agent.api.routes.requirements import (  # noqa: E402,F401
    search_requirement_features,
    search_requirements,
)
from src.requirement_agent.api.routes.requirements import search_router as _requirements_search_router  # noqa: E402

router.include_router(_requirements_search_router)


# —— 文档组 ——（documents：list / search / {id} / {id}/chunks / {id}/reindex）
from src.requirement_agent.api.routes.documents import (  # noqa: E402,F401
    get_document,
    get_document_chunks,
    list_documents,
    reindex_document_chunks,
    search_document_chunks,
)
from src.requirement_agent.api.routes.documents import router as _documents_router  # noqa: E402

router.include_router(_documents_router)


# —— 会话域 ——（conversations）
from src.requirement_agent.api.routes.conversations import (  # noqa: E402,F401
    add_conversation_message,
    create_conversation,
    delete_conversation,
    finalize_conversation,
    get_conversation_messages,
    list_conversations,
    update_conversation,
)
from src.requirement_agent.api.routes.conversations import router as _conversations_router  # noqa: E402

router.include_router(_conversations_router)


# —— 记忆组 ——（memory：list / upsert / delete / context）
from src.requirement_agent.api.routes.memory import (  # noqa: E402,F401
    delete_memory,
    get_memory_context,
    list_memory,
    upsert_memory,
)
from src.requirement_agent.api.routes.memory import router as _memory_router  # noqa: E402

router.include_router(_memory_router)


# —— 审核只读查询组 ——（reviews.router：pending / {id}/detail）
from src.requirement_agent.api.routes.reviews import get_review_detail, list_pending_reviews  # noqa: E402,F401
from src.requirement_agent.api.routes.reviews import router as _reviews_read_router  # noqa: E402

router.include_router(_reviews_read_router)


# —— 需求来源追踪组 ——（sources：`/api/v1/sources/{id}/trace`）
from src.requirement_agent.api.routes.sources import get_source_trace  # noqa: E402,F401
from src.requirement_agent.api.routes.sources import router as _sources_router  # noqa: E402

router.include_router(_sources_router)


# —— 只读需求查询组 ——（requirements.router：versions / features / diff / trace）
from src.requirement_agent.api.routes.requirements import (  # noqa: E402,F401
    get_requirement_diff,
    get_requirement_trace,
    list_requirement_features,
    list_requirement_versions,
)
from src.requirement_agent.api.routes.requirements import router as _requirements_query_router  # noqa: E402

router.include_router(_requirements_query_router)


# —— 审计事件查询组 ——（audit：`/api/v1/audit/events`）
from src.requirement_agent.api.routes.audit import list_audit_events  # noqa: E402,F401
from src.requirement_agent.api.routes.audit import router as _audit_router  # noqa: E402

router.include_router(_audit_router)


# —— 审核提交（写库 / 强事务）——（reviews.submit_router：`/api/v1/reviews/submit`）
from src.requirement_agent.api.routes.reviews import submit_review_decision  # noqa: E402,F401
from src.requirement_agent.api.routes.reviews import submit_router as _reviews_submit_router  # noqa: E402

router.include_router(_reviews_submit_router)
