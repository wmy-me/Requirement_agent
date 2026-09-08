from src.infrastructure.worker.outbox import OutboxEvent
from src.infrastructure.worker.tasks import DocumentChunkingTask


class FakeOutboxRepo:
    def __init__(self, events):
        self.events = events
        self.completed = []
        self.failed = []

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


class FakeDocumentRepo:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def add_chunks(self, document_id, text, *, chunk_size=600, overlap=120):
        if self.fail:
            raise RuntimeError("chunking unavailable")
        self.calls.append({"document_id": document_id, "text": text, "chunk_size": chunk_size, "overlap": overlap})
        return [{"document_id": document_id, "count": 1}]


def test_document_chunk_task_processes_pending_event() -> None:
    event = OutboxEvent(
        id=1,
        aggregate_type="document_asset",
        aggregate_id="42",
        event_type="document_chunk_sync",
        payload={"document_id": 42, "content": "需求标题：用户登录\n支持短信登录", "chunk_size": 600, "overlap": 120},
    )
    outbox_repo = FakeOutboxRepo([event])
    document_repo = FakeDocumentRepo()

    result = DocumentChunkingTask(outbox_repo=outbox_repo, document_repo=document_repo).process_pending()

    assert result == ["processed:1:42"]
    assert event.status == "completed"
    assert document_repo.calls[0]["document_id"] == 42
    assert "短信登录" in document_repo.calls[0]["text"]


def test_document_chunk_task_retries_then_dead_letters_failed_event() -> None:
    event = OutboxEvent(
        id=2,
        aggregate_type="document_asset",
        aggregate_id="99",
        event_type="document_chunk_sync",
        payload={"document_id": 99, "content": "审批流说明", "chunk_size": 600, "overlap": 120},
        retries=2,
    )
    outbox_repo = FakeOutboxRepo([event])

    result = DocumentChunkingTask(outbox_repo=outbox_repo, document_repo=FakeDocumentRepo(fail=True)).process_pending(max_retries=3)

    assert result == ["dead_letter:2:99"]
    assert event.status == "dead_letter"
    assert event.last_error == "chunking unavailable"
