"""通用 API Schema（健康检查等）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """健康检查响应体。"""

    status: str = Field(default="ok")
