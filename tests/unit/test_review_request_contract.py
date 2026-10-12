"""审核请求的字符串 snowflake 边界。

前端若把 63-bit ID 变成 Number，数字会在请求前失真；因此此处同时钉住
Pydantic 入参和路由交给领域层的精确转换，避免只测到其中一半。
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from requirement_agent.api.routes import reviews
from requirement_agent.api.schemas.reviews import ReviewSubmitRequest


SOURCE_ID = "9007199254740993123"  # 大于 Number.MAX_SAFE_INTEGER，且不是 2 的安全倍数。


def test_review_request_preserves_large_source_id_as_text() -> None:
    payload = ReviewSubmitRequest.model_validate({"source_id": SOURCE_ID, "decision": "rejected"})

    assert payload.source_id == SOURCE_ID
    assert isinstance(payload.source_id, str)


def test_review_request_rejects_numeric_source_id() -> None:
    """不接受 JSON number，强制调用方在网络边界保持字符串。"""
    with pytest.raises(ValidationError):
        ReviewSubmitRequest.model_validate({"source_id": int(SOURCE_ID), "decision": "rejected"})


def test_review_route_converts_large_source_id_only_inside_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    received: dict[str, object] = {}

    class FakeReviewService:
        def submit_decision(self, **kwargs: object) -> dict[str, object]:
            received.update(kwargs)
            return {"requirement_key": "REQ-TEST"}

    monkeypatch.setattr(reviews, "review_service", FakeReviewService())
    payload = ReviewSubmitRequest.model_validate({"source_id": SOURCE_ID, "decision": "rejected"})

    assert asyncio.run(reviews.submit_review_decision(payload)) == {"requirement_key": "REQ-TEST"}
    assert received["source_id"] == int(SOURCE_ID)
