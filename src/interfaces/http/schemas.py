from __future__ import annotations

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = Field(default="ok")


class RequirementSubmitRequest(BaseModel):
    source_type: str = Field(default="web")
    requester_id: str | None = None
    requester_name: str | None = None
    original_text: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class RequirementSubmitResponse(BaseModel):
    message: str
    source_type: str
    status: str = "accepted"
