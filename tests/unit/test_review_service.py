import pytest

from requirement_agent.application.review_service import ReviewService
from requirement_agent.domain.requirement import RequirementMaster, RequirementReview, RequirementSource, RequirementVersion


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
            extracted_text="需求标题：短信登录\n\n用户登录需要支持短信验证码",
            metadata={
                "requirement_key": "REQ-000001",
                "extracted": {
                    "requirement_title": "短信登录",
                    "summary": "用户登录需要支持短信验证码",
                    "requirements": ["支持短信验证码登录", "记录验证码校验结果"],
                },
            },
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
        self.links = []

    def save(self, version, session=None):
        self.version = version
        version.requirement_id = version.requirement_id or 1
        version.id = 1
        return version

    def link_source(self, version_id, source_id, session=None):
        self.links.append((version_id, source_id))
        return None


class FakeFeatureRepo:
    def __init__(self):
        self.features = []

    def create_features(self, requirement_id, features, *, source_id, requirement_key, version_no, session):
        self.features = [
            {
                "id": index,
                "requirement_id": requirement_id,
                "feature_key": f"F-{index:03d}",
                "content": content,
                "status": "active",
                "ordinal": index,
                "origin_source_id": source_id,
                "origin_requirement_key": requirement_key,
                "origin_version_no": version_no,
                "removed_version_no": None,
                "provenance": [{"version_no": version_no, "source_id": source_id, "kind": "add"}],
            }
            for index, content in enumerate(features, start=1)
        ]
        return self.features

    def list_active(self, requirement_id, session=None):
        return [item for item in self.features if item["requirement_id"] == requirement_id and item["status"] == "active"]

    def sync_features(self, requirement_id, features, *, source_id, requirement_key, version_no, session):
        self.create_features(
            requirement_id,
            features,
            source_id=source_id,
            requirement_key=requirement_key,
            version_no=version_no,
            session=session,
        )
        return [{"op": "modify", "feature_key": item["feature_key"], "after": item["content"]} for item in self.features]

    def apply_overrides(self, requirement_id, overrides, *, source_id, requirement_key, version_no, session):
        changes = []
        for item in overrides:
            op = item["op"]
            feature_key = item.get("feature_key")
            if op == "modify":
                for feature in self.features:
                    if feature["feature_key"] == feature_key:
                        changes.append(
                            {
                                "op": "modify",
                                "feature_key": feature_key,
                                "before": feature["content"],
                                "after": item["content"],
                            }
                        )
                        feature["content"] = item["content"]
            elif op == "delete":
                for feature in self.features:
                    if feature["feature_key"] == feature_key:
                        feature["status"] = "deleted"
                        changes.append({"op": "delete", "feature_key": feature_key, "content": feature["content"]})
            elif op == "add":
                next_id = len(self.features) + 1
                created = {
                    "id": next_id,
                    "requirement_id": requirement_id,
                    "feature_key": feature_key or f"F-{next_id:03d}",
                    "content": item["content"],
                    "status": "active",
                    "ordinal": next_id,
                    "origin_source_id": source_id,
                    "origin_requirement_key": requirement_key,
                    "origin_version_no": version_no,
                    "removed_version_no": None,
                    "provenance": [{"version_no": version_no, "source_id": source_id, "kind": "add"}],
                }
                self.features.append(created)
                changes.append({"op": "add", "feature_key": created["feature_key"], "content": created["content"]})
        return changes

    def join_active_features(self, requirement_id, *, session=None):
        return "\n".join(item["content"] for item in self.list_active(requirement_id, session=session))


class FakeAuditRepo:
    def __init__(self):
        self.events = []

    def record(self, event, session=None):
        self.events.append(event)
        return event


class FakeOutboxRepo:
    def __init__(self):
        self.events = []

    def enqueue(self, **kwargs):
        self.events.append(kwargs)
        return kwargs


class FailingAuditRepo(FakeAuditRepo):
    def record(self, event, session=None):
        raise RuntimeError("audit write failed")


def test_review_service_approves_and_commits_version() -> None:
    session = FakeSession()
    source_repo = FakeSourceRepo()
    master_repo = FakeMasterRepo()
    version_repo = FakeVersionRepo()
    outbox_repo = FakeOutboxRepo()
    feature_repo = FakeFeatureRepo()
    review_service = ReviewService(
        review_repo=FakeReviewRepo(),
        source_repo=source_repo,
        master_repo=master_repo,
        feature_repo=feature_repo,
        version_repo=version_repo,
        audit_repo=FakeAuditRepo(),
        outbox_repo=outbox_repo,
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
    assert version_repo.version.requirement_snapshot == "新增手机号登录与短信验证码认证能力"
    assert version_repo.version.feature_changes == [{"op": "add", "feature_key": "F-001", "content": "新增手机号登录与短信验证码认证能力"}]
    assert version_repo.version.diff_payload["source_id"] == 1
    assert version_repo.links == [(1, 1)]
    assert source_repo.source.metadata["trace"] == {
        "source_id": 1,
        "requirement_key": "REQ-000001",
        "version_id": 1,
        "version_no": 1,
        "relation_type": "source",
    }
    assert outbox_repo.events[0]["event_type"] == "embedding_sync"
    assert outbox_repo.events[0]["payload"]["requirement_id"] == 1
    assert outbox_repo.events[0]["payload"]["content"] == "新增手机号登录与短信验证码认证能力"
    assert session.commits == 1
    assert session.rollbacks == 0
    assert session.closed is True


def test_review_service_uses_structured_extraction_without_manual_edit() -> None:
    session = FakeSession()
    master_repo = FakeMasterRepo()
    master_repo.master = None
    version_repo = FakeVersionRepo()
    outbox_repo = FakeOutboxRepo()
    feature_repo = FakeFeatureRepo()
    review_service = ReviewService(
        review_repo=FakeReviewRepo(),
        source_repo=FakeSourceRepo(),
        master_repo=master_repo,
        feature_repo=feature_repo,
        version_repo=version_repo,
        audit_repo=FakeAuditRepo(),
        outbox_repo=outbox_repo,
        session_factory=lambda: session,
    )

    result = review_service.submit_decision(
        source_id=1,
        decision="approved",
        reviewer_id="manager",
    )

    assert result["requirement_key"] == "REQ-000002"
    assert master_repo.master.requirement_name == "短信登录"
    assert version_repo.version.requirement_snapshot == "支持短信验证码登录\n记录验证码校验结果"
    assert feature_repo.join_active_features(1) == "支持短信验证码登录\n记录验证码校验结果"
    assert outbox_repo.events[0]["aggregate_id"] == "REQ-000002"


def test_review_service_merges_into_existing_requirement_with_feature_overrides() -> None:
    session = FakeSession()
    source_repo = FakeSourceRepo()
    master_repo = FakeMasterRepo()
    master_repo.master.current_version = 1
    feature_repo = FakeFeatureRepo()
    feature_repo.features = [
        {
            "id": 1,
            "requirement_id": 1,
            "feature_key": "F-001",
            "content": "支持短信验证码登录",
            "status": "active",
            "ordinal": 1,
            "origin_source_id": 1,
            "origin_requirement_key": "REQ-000001",
            "origin_version_no": 1,
            "removed_version_no": None,
            "provenance": [{"version_no": 1, "source_id": 1, "kind": "add"}],
        }
    ]
    version_repo = FakeVersionRepo()
    review_service = ReviewService(
        review_repo=FakeReviewRepo(),
        source_repo=source_repo,
        master_repo=master_repo,
        feature_repo=feature_repo,
        version_repo=version_repo,
        audit_repo=FakeAuditRepo(),
        outbox_repo=FakeOutboxRepo(),
        session_factory=lambda: session,
    )

    result = review_service.submit_decision(
        source_id=1,
        decision="approved",
        reviewer_id="manager",
        target_requirement_key="REQ-000001",
        feature_overrides=[
            {"feature_key": "F-001", "op": "modify", "content": "支持手机号登录与短信验证码登录"},
            {"op": "add", "content": "记录验证码校验结果"},
        ],
    )

    assert result["requirement_key"] == "REQ-000001"
    assert result["version_no"] == 2
    assert version_repo.version.change_type == "modify"
    assert version_repo.version.parent_version_no == 1
    assert version_repo.version.requirement_snapshot == "支持手机号登录与短信验证码登录\n记录验证码校验结果"
    assert version_repo.version.feature_changes == [
        {
            "op": "modify",
            "feature_key": "F-001",
            "before": "支持短信验证码登录",
            "after": "支持手机号登录与短信验证码登录",
        },
        {"op": "add", "feature_key": "F-002", "content": "记录验证码校验结果"},
    ]


def test_review_service_rolls_back_when_audit_write_fails() -> None:
    session = FakeSession()
    review_service = ReviewService(
        review_repo=FakeReviewRepo(),
        source_repo=FakeSourceRepo(),
        master_repo=FakeMasterRepo(),
        feature_repo=FakeFeatureRepo(),
        version_repo=FakeVersionRepo(),
        audit_repo=FailingAuditRepo(),
        outbox_repo=FakeOutboxRepo(),
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
