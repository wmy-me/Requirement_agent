from __future__ import annotations

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    limit: int = Field(default=10, ge=1, le=50)


class ReviewRequest(BaseModel):
    source_id: int = Field(gt=0)
    decision: str = Field(pattern="^(approved|rejected|returned)$")
    reviewer_name: str | None = Field(default=None, max_length=120)
    comment: str | None = Field(default=None, max_length=5_000)
    edited_requirement: str | None = Field(default=None, max_length=20_000)
