from __future__ import annotations

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    limit: int = Field(default=10, ge=1, le=50)


class ReviewRequest(BaseModel):
    decision: str = Field(pattern="^(approved|rejected|returned)$")
    reviewer_id: str
    reviewer_name: str | None = None
    comment: str | None = None
