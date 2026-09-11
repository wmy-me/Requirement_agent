"""兼容转发：Agent 聊天域已迁至 `requirement_agent.api.routes.agent_chat`（子批次 3.3.5）。

`from src.interfaces.http.agent_chat import router`（及下列路由函数名）仍可用，
与新版为**同一对象**。
"""

from __future__ import annotations

from src.requirement_agent.api.routes.agent_chat import (  # noqa: F401
    chat_with_agent,
    get_agent_chat_history,
    get_agent_run,
    router,
    run_agent_pipeline,
    stream_agent_chat,
    stream_agent_chat_with_files,
)

__all__ = ["router"]
