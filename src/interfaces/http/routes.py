"""HTTP 路由聚合入口。

原单文件 `routes.py`（约 1130 行）按资源域拆分为：
- `agent_chat.py`：Agent 对话域（/agent/*，含 SSE 流式生成器）。
- `rest.py`：其余 REST 端点（requirements / documents / conversations / memory / reviews / audit / health）。
- `_state.py`：共享领域单例与会话内存态。

本模块仅负责把各子 router 聚合到统一的 `router`，供 `main.py` 挂载；
保持对外 import 路径（`from src.interfaces.http.routes import router`）不变。
"""

from __future__ import annotations

from fastapi import APIRouter

from src.interfaces.http.agent_chat import router as agent_router
from src.interfaces.http.rest import router as rest_router

router = APIRouter(tags=["requirements"])
router.include_router(rest_router)
router.include_router(agent_router)

__all__ = ["router"]
