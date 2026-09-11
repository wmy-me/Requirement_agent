from src.requirement_agent.application.retrieval_service import RetrievalService
from src.requirement_agent.infrastructure.vector.pgvector_repository import RequirementVectorRepository


class FakeMasterRepo:
    def list_with_source_context(self):
        return [
            {
                "requirement_key": "REQ-000001",
                "requirement_name": "短信登录",
                "final_requirement": "用户登录需要支持短信验证码",
                "status": "active",
                "current_version": 2,
                "feature_count": 2,
                "source_types": ["web"],
                "requester_names": ["alice"],
                "departments": ["用户体验部"],
                "business_domains": ["auth"],
                "sensitivity_levels": ["internal"],
                "feature_contents": ["支持短信验证码登录", "记录验证码校验结果"],
                "latest_source_submitted_at": "2026-09-07T10:00:00+08:00",
            },
            {
                "requirement_key": "REQ-000002",
                "requirement_name": "报表导出",
                "final_requirement": "运营报表支持导出",
                "status": "active",
                "current_version": 1,
                "feature_count": 1,
                "source_types": ["email"],
                "requester_names": ["bob"],
                "departments": ["运营部"],
                "business_domains": ["report"],
                "sensitivity_levels": ["confidential"],
                "feature_contents": ["支持按部门筛选导出"],
                "latest_source_submitted_at": "2026-09-06T10:00:00+08:00",
            },
        ]


class FakeFeatureRepo:
    def search_features(self, query, *, status=None, requester=None, has_version_ge=None, limit=20):
        return [
            {
                "feature_key": "F-001",
                "content": "支持短信验证码登录",
                "status": status or "active",
                "ordinal": 1,
                "origin_source_id": 1,
                "origin_requirement_key": "REQ-000001",
                "origin_version_no": 1,
                "removed_version_no": None,
                "provenance": [],
                "requirement_key": "REQ-000001",
                "requirement_name": "短信登录",
                "current_version": has_version_ge or 2,
                "requirement_status": "active",
            }
        ][:limit]


class FakeEmbeddingService:
    def embed(self, query):
        return [0.1] * 1536


class FakeVectorRepo:
    def __init__(self):
        self.filters = None

    def search(self, vector, limit=10, filters=None):
        self.filters = filters
        return []


def test_retrieval_filters_keyword_candidates_by_metadata() -> None:
    vector_repo = FakeVectorRepo()
    service = RetrievalService(
        vector_repo=vector_repo,
        embedding_service=FakeEmbeddingService(),
        master_repo=FakeMasterRepo(),
        feature_repo=FakeFeatureRepo(),
    )

    result = service.search(
        "登录",
        filters={
            "channel": "web",
            "requester": "alice",
            "department": "用户体验部",
            "business_domain": "auth",
            "has_version_ge": 2,
            "sensitivity_level": "internal",
            "submitted_from": "2026-09-07T00:00:00+08:00",
        },
    )

    assert [item["requirement_key"] for item in result] == ["REQ-000001"]
    assert result[0]["departments"] == ["用户体验部"]
    assert result[0]["matched_features"] == ["支持短信验证码登录"]
    assert vector_repo.filters["department"] == "用户体验部"
    assert vector_repo.filters["requester"] == "alice"


def test_vector_repository_builds_metadata_filter_sql() -> None:
    where_clause, params = RequirementVectorRepository(dsn="postgresql://example")._build_metadata_filter(
        {
            "channel": "email",
            "requester": "bob",
            "department": "运营部",
            "business_domain": "report",
            "sensitivity_level": "confidential",
            "has_version_ge": 3,
            "submitted_from": "2026-09-01T00:00:00+08:00",
            "submitted_to": "2026-09-08T00:00:00+08:00",
        }
    )

    assert "s2.source_type = %s" in where_clause
    assert "s4.submitted_at >= %s::timestamptz" in where_clause
    assert "s5.submitted_at <= %s::timestamptz" in where_clause
    assert "s6.requester_name = %s OR s6.requester_id = %s" in where_clause
    assert "rm.current_version >= %s" in where_clause
    assert params[0] == "email"
    assert "运营部" in params
    assert "report" in params
    assert "confidential" in params
    assert "bob" in params
    assert 3 in params


def test_retrieval_service_supports_feature_search() -> None:
    service = RetrievalService(
        vector_repo=FakeVectorRepo(),
        embedding_service=FakeEmbeddingService(),
        master_repo=FakeMasterRepo(),
        feature_repo=FakeFeatureRepo(),
    )

    result = service.search_features("验证码", requester="alice", has_version_ge=2)

    assert result[0]["requirement_key"] == "REQ-000001"
    assert result[0]["feature_key"] == "F-001"
