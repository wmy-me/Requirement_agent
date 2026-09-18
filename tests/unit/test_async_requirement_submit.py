"""异步文本提交：HTTP 接收不应等待分析图或模型调用。"""

from __future__ import annotations

import asyncio

from requirement_agent.api.routes import requirements_write
from requirement_agent.api.schemas import RequirementSubmitRequest
from requirement_agent.domain.requirement import RequirementSource


class _Service:
    def __init__(self, source: RequirementSource) -> None:
        self.source = source
        self.accepted: RequirementSource | None = None

    def accept_requirement(self, source: RequirementSource) -> RequirementSource:
        self.accepted = source
        return self.source


class _Task:
    def __init__(self) -> None:
        self.source_ids: list[int] = []

    def enqueue(self, *, source_id: int) -> str:
        self.source_ids.append(source_id)
        return "event-1"


def test_async_submit_accepts_and_queues_without_running_analysis(monkeypatch) -> None:
    saved = RequirementSource(
        idempotency_key="test", source_type="web", id=225548242094391297,
        processing_status="received",
    )
    service = _Service(saved)
    task = _Task()
    monkeypatch.setattr(requirements_write, "requirement_service", service)
    monkeypatch.setattr(requirements_write, "requirement_analysis_task", task)

    result = asyncio.run(requirements_write.submit_requirement_async(RequirementSubmitRequest(
        original_text="希望按部门查看报表", requester_name="alice",
    )))

    assert result.status == "received"
    assert result.queued is True
    assert result.source_id == "225548242094391297"
    assert task.source_ids == [225548242094391297]
    assert service.accepted is not None
    assert "alice" in service.accepted.idempotency_key


def test_async_submit_does_not_requeue_an_existing_source(monkeypatch) -> None:
    saved = RequirementSource(
        idempotency_key="test", source_type="web", id=225548242094391297,
        processing_status="pending_review",
    )
    task = _Task()
    monkeypatch.setattr(requirements_write, "requirement_service", _Service(saved))
    monkeypatch.setattr(requirements_write, "requirement_analysis_task", task)

    result = asyncio.run(requirements_write.submit_requirement_async(RequirementSubmitRequest(
        original_text="希望按部门查看报表",
    )))

    assert result.queued is False
    assert task.source_ids == []
