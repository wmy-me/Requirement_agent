"""HTTP 路由聚合入口。

保留与原结构一致的**两级聚合**（tags 行为不变）：
- `rest_router`：REST 域各子 router 的有序聚合（对应原 `src/interfaces/http/rest.py` 的 router）；
- `router`：总聚合 = `rest_router` + agent 域（对应原 `src/interfaces/http/routes.py` 的 router）。

`api/app.py` 挂载 `router`。
"""

from __future__ import annotations

from fastapi import APIRouter

from requirement_agent.api.routes.agent_chat import router as _agent_chat_router
from requirement_agent.api.routes.audit import router as _audit_router
from requirement_agent.api.routes.capabilities import router as _capabilities_router
from requirement_agent.api.routes.channels import router as _channels_router
from requirement_agent.api.routes.conversations import router as _conversations_router
from requirement_agent.api.routes.documents import router as _documents_router
from requirement_agent.api.routes.health import router as _health_router
from requirement_agent.api.routes.memory import router as _memory_router
from requirement_agent.api.routes.ops import router as _ops_router
from requirement_agent.api.routes.requirements import router as _requirements_query_router
from requirement_agent.api.routes.requirements import search_router as _requirements_search_router
from requirement_agent.api.routes.requirements_write import router as _requirements_write_router
from requirement_agent.api.routes.reviews import router as _reviews_read_router
from requirement_agent.api.routes.reviews import submit_router as _reviews_submit_router
from requirement_agent.api.routes.sources import router as _sources_router
from requirement_agent.api.routes.stats import router as _stats_router
from requirement_agent.api.routes.titles import router as _titles_router
from requirement_agent.api.routes.system import router as _system_router


# —— REST 域聚合（注册顺序与迁移前完全一致）——
rest_router = APIRouter(tags=["requirements"])
rest_router.include_router(_system_router)
rest_router.include_router(_health_router)
rest_router.include_router(_requirements_write_router)
rest_router.include_router(_requirements_search_router)
rest_router.include_router(_documents_router)
rest_router.include_router(_conversations_router)
rest_router.include_router(_memory_router)
rest_router.include_router(_reviews_read_router)
rest_router.include_router(_sources_router)
rest_router.include_router(_requirements_query_router)
rest_router.include_router(_audit_router)
rest_router.include_router(_reviews_submit_router)
# 渠道 Webhook 追加在末尾：不打扰既有路由的注册顺序
rest_router.include_router(_channels_router)
rest_router.include_router(_ops_router)
# 能力 / 条件词表（批次 1）：纯新增只读路由，追加在末尾
rest_router.include_router(_capabilities_router)
rest_router.include_router(_titles_router)
# 总览聚合（前端工作台）：纯新增只读路由，追加在末尾
rest_router.include_router(_stats_router)


# —— 总聚合：REST 域 + Agent 域 ——
router = APIRouter(tags=["requirements"])
router.include_router(rest_router)
router.include_router(_agent_chat_router)

__all__ = ["router", "rest_router"]
