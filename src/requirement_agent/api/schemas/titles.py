"""候选标题的 API Schema。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TitleCandidateReviewRequest(BaseModel):
    """裁决一条候选标题。只接受 confirmed / dismissed（撤回要重新派生）。"""

    model_config = ConfigDict(extra="forbid")

    status: Literal["confirmed", "dismissed"]


class TitleCandidateCreateRequest(BaseModel):
    """人工新增一条候选标题。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
