from src.requirement_agent.infrastructure.worker.outbox import OutboxEvent
from src.requirement_agent.infrastructure.worker.tasks import EmbeddingTask


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


class FakeEmbeddingService:
    def embed(self, text):
        return [0.1] * 1536


class FakeVectorRepo:
    def __init__(self, fail=False):
        self.fail = fail
        self.upserts = []

    def upsert(self, requirement_id, embedding, *, source_text):
        if self.fail:
            raise RuntimeError("vector unavailable")
        self.upserts.append((requirement_id, embedding, source_text))
        return {"requirement_id": requirement_id, "source_text": source_text}


def test_embedding_task_processes_pending_event() -> None:
    event = OutboxEvent(
        id=1,
        aggregate_type="requirement_master",
        aggregate_id="REQ-000001",
        event_type="embedding_sync",
        payload={"requirement_id": 10, "content": "短信登录"},
    )
    outbox_repo = FakeOutboxRepo([event])
    vector_repo = FakeVectorRepo()

    result = EmbeddingTask(
        outbox_repo=outbox_repo,
        embedding_service=FakeEmbeddingService(),
        vector_repo=vector_repo,
    ).process_pending()

    assert result == ["processed:1:REQ-000001"]
    assert event.status == "completed"
    assert vector_repo.upserts[0][0] == 10
    assert vector_repo.upserts[0][2] == "短信登录"


def test_embedding_task_retries_then_dead_letters_failed_event() -> None:
    event = OutboxEvent(
        id=2,
        aggregate_type="requirement_master",
        aggregate_id="REQ-000002",
        event_type="embedding_sync",
        payload={"requirement_id": 11, "content": "审批流"},
        retries=2,
    )
    outbox_repo = FakeOutboxRepo([event])

    result = EmbeddingTask(
        outbox_repo=outbox_repo,
        embedding_service=FakeEmbeddingService(),
        vector_repo=FakeVectorRepo(fail=True),
    ).process_pending(max_retries=3)

    assert result == ["dead_letter:2:REQ-000002"]
    assert event.status == "dead_letter"
    assert event.last_error == "vector unavailable"
