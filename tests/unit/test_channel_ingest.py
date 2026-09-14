"""渠道接入单测：载荷归一化、幂等去重、异步入队与消费短路。

沿用仓库既有做法——手写 Fake 注入，不引入 mock 库、不连数据库。
"""

import pytest

from requirement_agent.application.channel_service import ChannelIngestService
from requirement_agent.application.requirement_service import RequirementService
from requirement_agent.domain.requirement import RequirementSource
from requirement_agent.infrastructure.channels.base import InboundRequirement
from requirement_agent.infrastructure.channels.feishu_client import FeishuClient
from requirement_agent.infrastructure.worker.outbox import OutboxEvent
from requirement_agent.infrastructure.worker.tasks import RequirementAnalysisTask


# ── 载荷归一化 ──────────────────────────────────────────────────────────


def test_feishu_parse_v2_payload() -> None:
    """v2 事件：正文在 message.content（一段 JSON 字符串），幂等键取 header.event_id。"""
    inbound = FeishuClient().parse(
        {
            "schema": "2.0",
            "header": {"event_id": "evt-1", "event_type": "im.message.receive_v1"},
            "event": {
                "sender": {"sender_id": {"open_id": "ou_abc"}},
                "message": {"message_type": "text", "content": '{"text":"希望支持批量导出"}'},
            },
        }
    )

    assert inbound.channel == "feishu"
    assert inbound.text == "希望支持批量导出"
    assert inbound.event_id == "evt-1"
    assert inbound.requester_id == "ou_abc"
    assert inbound.metadata["event_type"] == "im.message.receive_v1"


def test_feishu_parse_flat_payload() -> None:
    """扁平结构（内部联调/单测用）：没有 header 时也应能取到正文。"""
    inbound = FeishuClient().parse({"text": "扁平正文", "user_name": "alice"})
    assert inbound.text == "扁平正文"
    assert inbound.event_id is None
    assert inbound.requester_name == "alice"


def test_feishu_parse_tolerates_non_json_content() -> None:
    """content 不是 JSON 时按纯文本处理，不能抛异常。"""
    inbound = FeishuClient().parse({"event": {"message": {"content": "不是JSON"}}})
    assert inbound.text == "不是JSON"


def test_feishu_verify_fails_closed_when_unconfigured() -> None:
    """未配置 Encrypt Key 时无从验签——必须拒绝，而不是沿用基类的默认放行。"""
    assert FeishuClient().verify({}, b"{}") is False
    assert FeishuClient().is_configured is False


def test_inbound_requirement_rejects_empty_text() -> None:
    assert InboundRequirement(channel="feishu", text="   ").is_usable() is False
    assert InboundRequirement(channel="feishu", text="有内容").is_usable() is True


# ── 接入服务：落库 / 幂等 / 入队 ────────────────────────────────────────


class FakeSourceRepo:
    def __init__(self, existing: RequirementSource | None = None) -> None:
        self.existing = existing
        self.lookups: list[tuple[str, str | None]] = []

    def find_by_channel_event(self, source_type: str, source_event_id: str | None):
        self.lookups.append((source_type, source_event_id))
        return self.existing


class FakeRequirementService:
    """只实现渠道路径用到的那一段：落库但不分析。"""

    def __init__(self, status: str = "received") -> None:
        self.status = status
        self.accepted: list[RequirementSource] = []

    def accept_requirement(self, source: RequirementSource) -> RequirementSource:
        self.accepted.append(source)
        source.id = 1001
        source.processing_status = self.status
        return source


class FakeAnalysisTask:
    def __init__(self) -> None:
        self.enqueued: list[int] = []

    def enqueue(self, *, source_id: int) -> str:
        self.enqueued.append(source_id)
        return f"queued:{source_id}"


def _service(repo: FakeSourceRepo | None = None, status: str = "received"):
    repo = repo or FakeSourceRepo()
    req_service = FakeRequirementService(status=status)
    task = FakeAnalysisTask()
    service = ChannelIngestService(analysis_task=task, source_repo=repo, requirement_service=req_service)
    return service, repo, req_service, task


def test_ingest_new_event_saves_and_enqueues() -> None:
    service, _repo, req_service, task = _service()

    result = service.ingest(InboundRequirement(channel="feishu", text="新需求", event_id="evt-1"))

    assert result == {
        "accepted": True,
        "deduplicated": False,
        "queued": True,
        "source_id": 1001,
        "status": "received",
        "channel": "feishu",
    }
    assert task.enqueued == [1001]
    saved = req_service.accepted[0]
    assert saved.source_type == "feishu"
    assert saved.source_event_id == "evt-1"
    assert saved.idempotency_key == "feishu:event:evt-1"


def test_ingest_duplicate_event_is_deduplicated_and_not_requeued() -> None:
    existing = RequirementSource(
        idempotency_key="k", source_type="feishu", id=999, processing_status="pending_review"
    )
    service, _repo, req_service, task = _service(FakeSourceRepo(existing=existing))

    result = service.ingest(InboundRequirement(channel="feishu", text="重复投递", event_id="evt-1"))

    assert result["deduplicated"] is True
    assert result["source_id"] == 999
    assert result["queued"] is False
    assert task.enqueued == []  # 不重复入队
    assert req_service.accepted == []  # 也不重复落库


def test_ingest_empty_text_is_rejected() -> None:
    service, _repo, _req_service, task = _service()

    result = service.ingest(InboundRequirement(channel="feishu", text="   ", event_id="evt-2"))

    assert result == {"accepted": False, "reason": "empty_text", "channel": "feishu"}
    assert task.enqueued == []


def test_ingest_without_event_id_falls_back_to_content_key() -> None:
    service, repo, req_service, _task = _service()

    service.ingest(InboundRequirement(channel="feishu", text="无事件号", requester_id="u1"))

    assert repo.lookups == []  # 没有事件 ID 就不查渠道幂等
    assert req_service.accepted[0].idempotency_key == "feishu:u1:无事件号"


def test_ingest_does_not_requeue_when_source_already_advanced() -> None:
    """同 idempotency_key 的重复提交会带回已推进的状态，此时不该再入队。"""
    service, _repo, _req_service, task = _service(status="pending_review")

    result = service.ingest(InboundRequirement(channel="feishu", text="已处理过"))

    assert result["queued"] is False
    assert task.enqueued == []


# ── 异步分析任务 ────────────────────────────────────────────────────────


class FakeOutboxRepo:
    def __init__(self, events: list[OutboxEvent]) -> None:
        self.events = events
        self.enqueued: OutboxEvent | None = None
        self.completed: list[OutboxEvent] = []
        self.failed: list[OutboxEvent] = []

    def enqueue(self, *, aggregate_type, aggregate_id, event_type, payload=None, session=None):
        event = OutboxEvent(
            id=77,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=payload or {},
        )
        self.enqueued = event
        self.events.append(event)
        return event

    def claim_pending(self, *, limit=20, event_type=None):
        return [
            event
            for event in self.events[:limit]
            if event.status == "pending" and (event_type is None or event.event_type == event_type)
        ]

    def mark_done(self, event):
        event.status = "completed"
        self.completed.append(event)
        return event

    def mark_failed(self, event, *, error, max_retries=3):
        event.retries += 1
        event.status = "dead_letter" if event.retries >= max_retries else "pending"
        event.last_error = error
        self.failed.append(event)
        return event


def test_analysis_task_enqueue_writes_expected_event() -> None:
    repo = FakeOutboxRepo([])
    task = RequirementAnalysisTask(lambda source_id: {}, outbox_repo=repo)

    assert task.enqueue(source_id=42) == "queued:77:42:requirement_analysis"
    assert repo.enqueued is not None
    assert repo.enqueued.event_type == "requirement_analysis"
    assert repo.enqueued.aggregate_type == "requirement_source"
    assert repo.enqueued.payload == {"source_id": 42}


def test_analysis_task_processes_pending_event() -> None:
    event = OutboxEvent(
        id=5,
        aggregate_type="requirement_source",
        aggregate_id="42",
        event_type="requirement_analysis",
        payload={"source_id": 42},
    )
    repo = FakeOutboxRepo([event])
    seen: list[int] = []

    results = RequirementAnalysisTask(seen.append, outbox_repo=repo).process_pending()

    assert results == ["processed:5:42"]
    assert seen == [42]
    assert event.status == "completed"


def test_analysis_task_marks_failure_for_retry() -> None:
    event = OutboxEvent(
        id=6,
        aggregate_type="requirement_source",
        aggregate_id="43",
        event_type="requirement_analysis",
        payload={"source_id": 43},
    )
    repo = FakeOutboxRepo([event])

    def boom(source_id: int) -> dict[str, object]:
        raise RuntimeError("llm down")

    results = RequirementAnalysisTask(boom, outbox_repo=repo).process_pending()

    assert results == ["pending:6:43"]
    assert event.last_error == "llm down"


def test_analysis_task_ignores_other_event_types() -> None:
    other = OutboxEvent(
        id=7, aggregate_type="requirement_master", aggregate_id="REQ-1", event_type="embedding_sync"
    )
    repo = FakeOutboxRepo([other])

    assert RequirementAnalysisTask(lambda source_id: {}, outbox_repo=repo).process_pending() == []


# ── 异步路径的幂等短路 ──────────────────────────────────────────────────


class FakeSourceRepoForProcess:
    def __init__(self, source: RequirementSource | None) -> None:
        self.source = source

    def get_by_id(self, source_id: int):
        return self.source


def test_process_requirement_short_circuits_when_already_advanced() -> None:
    """重复消费同一条事件时不能重跑分析图——来源已推进就直接回现状。"""
    source = RequirementSource(
        idempotency_key="k", source_type="feishu", id=7, processing_status="pending_review"
    )
    service = RequirementService(source_repo=FakeSourceRepoForProcess(source))

    result = service.process_requirement(7)

    assert result["source_id"] == 7
    assert result["status"] == "pending_review"
    assert "analysis" not in result


def test_process_requirement_raises_for_unknown_source() -> None:
    service = RequirementService(source_repo=FakeSourceRepoForProcess(None))

    with pytest.raises(ValueError):
        service.process_requirement(404)
