from src.application.review_service import ReviewService
from src.domain.requirement import RequirementMaster, RequirementReview, RequirementSource, RequirementVersion


class FakeReviewRepo:
    def __init__(self):
        self.records = []

    def save(self, review):
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
        )

    def get_by_id(self, source_id):
        return self.source


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

    def get_by_key(self, requirement_key):
        return self.master if self.master.requirement_key == requirement_key else None

    def save(self, requirement):
        if requirement.id is None:
            requirement.id = 1
        self.master = requirement
        return requirement


class FakeVersionRepo:
    def __init__(self):
        self.version = None

    def save(self, version):
        self.version = version
        version.requirement_id = version.requirement_id or 1
        return version


class FakeAuditRepo:
    def __init__(self):
        self.events = []

    def record(self, event):
        self.events.append(event)
        return event


def test_review_service_approves_and_commits_version() -> None:
    review_service = ReviewService(
        review_repo=FakeReviewRepo(),
        source_repo=FakeSourceRepo(),
        master_repo=FakeMasterRepo(),
        version_repo=FakeVersionRepo(),
        audit_repo=FakeAuditRepo(),
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
