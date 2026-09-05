import pytest

from src.application.review_service import ReviewService
from src.domain.requirement import RequirementMaster, RequirementReview, RequirementSource, RequirementVersion


class FakeSession:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class FakeReviewRepo:
    def __init__(self):
        self.records = []

    def save(self, review, session=None):
        self.records.append(review)
        return review


class FakeSourceRepo:
    def __init__(self):
        self.source = RequirementSource(
            idempotency_key="src-1",
            source_type="web",
            requester_id="alice",
            requester_name="alice",
            original_text="用户登录需求",
            metadata={"requirement_key": "REQ-000001"},
            processing_status="pending_review",
        )

    def get_by_id(self, source_id, session=None):
        self.source.id = source_id
        return self.source

    def update_status(self, source_id, processing_status, metadata=None, session=None):
        self.source.processing_status = processing_status
        if metadata is not None:
            self.source.metadata = metadata


class FakeMasterRepo:
    def __init__(self):
        self.master = RequirementMaster(
            id=1,
            requirement_key="REQ-000001",
            requirement_name="用户登录",
            final_requirement="用户登录需求",
            current_version=0,
            status="active",
            lock_version=0,
        )

    def allocate_key(self, session=None):
        return "REQ-000002"

    def get_by_key(self, requirement_key, session=None):
        return self.master if self.master.requirement_key == requirement_key else None

    def save(self, requirement, session=None):
        if requirement.id is None:
            requirement.id = 1
        self.master = requirement
        return requirement


class FakeVersionRepo:
    def __init__(self):
        self.version = None

    def save(self, version, session=None):
        self.version = version
        version.requirement_id = version.requirement_id or 1
        version.id = 1
        return version

    def link_source(self, version_id, source_id, session=None):
        return None


class FakeAuditRepo:
    def __init__(self):
        self.events = []

    def record(self, event, session=None):
        self.events.append(event)
        return event


class FailingAuditRepo(FakeAuditRepo):
    def record(self, event, session=None):
        raise RuntimeError("audit write failed")


def test_review_service_approves_and_commits_version() -> None:
    session = FakeSession()
    review_service = ReviewService(
        review_repo=FakeReviewRepo(),
        source_repo=FakeSourceRepo(),
        master_repo=FakeMasterRepo(),
        version_repo=FakeVersionRepo(),
        audit_repo=FakeAuditRepo(),
        session_factory=lambda: session,
    )

    result = review_service.submit_decision(
        source_id=1,
        decision="approved",
        reviewer_id="manager",
        reviewer_name="审核人A",
        comment="已确认并入库",
        edited_requirement="新增手机号登录与短信验证码认证能力",
        requirement_key="REQ-000001",
        analysis_snapshot={"duplicate": False, "risk": "low"},
    )

    assert result["status"] == "recorded"
    assert result["requirement_key"] == "REQ-000001"
    assert result["version_no"] == 1
    assert session.commits == 1
    assert session.rollbacks == 0
    assert session.closed is True


def test_review_service_rolls_back_when_audit_write_fails() -> None:
    session = FakeSession()
    review_service = ReviewService(
        review_repo=FakeReviewRepo(),
        source_repo=FakeSourceRepo(),
        master_repo=FakeMasterRepo(),
        version_repo=FakeVersionRepo(),
        audit_repo=FailingAuditRepo(),
        session_factory=lambda: session,
    )

    with pytest.raises(RuntimeError, match="audit write failed"):
        review_service.submit_decision(
            source_id=1,
            decision="approved",
            reviewer_id="manager",
        )

    assert session.commits == 0
    assert session.rollbacks == 1
    assert session.closed is True
