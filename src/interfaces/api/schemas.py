"""Schemas for the external HTTP API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RequirementCreateRequest(BaseModel):
    source_type: str = Field(default="web")
    requester_id: str | None = None
    requester_name: str | None = None
    original_text: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class RequirementCreateResponse(BaseModel):
    requirement_key: str
    status: str = "accepted"
