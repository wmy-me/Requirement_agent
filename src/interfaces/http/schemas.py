from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class HealthResponse(BaseModel):
    status: str = Field(default="ok")


class RequirementSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: Literal["web", "email", "meeting", "manual"] = "web"
    requester_id: str | None = Field(default=None, max_length=120)
    requester_name: str | None = Field(default=None, max_length=120)
    original_text: str = Field(min_length=1, max_length=20_000)
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("requester_id", "requester_name", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("original_text")
    @classmethod
    def require_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("original_text must not be blank")
        return normalized


class ReviewSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: int = Field(gt=0)
    decision: Literal["approved", "rejected", "returned"]
    reviewer_name: str | None = Field(default=None, max_length=120)
    comment: str | None = Field(default=None, max_length=5_000)
    edited_requirement: str | None = Field(default=None, max_length=20_000)

    @field_validator("reviewer_name", "comment", "edited_requirement", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class RequirementSubmitResponse(BaseModel):
    message: str
    source_type: str
    status: str = "pending_review"
    source_id: int | None = None
