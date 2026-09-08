from src.application.retrieval_service import RetrievalService
from src.infrastructure.vector.pgvector_repository import RequirementVectorRepository


class FakeMasterRepo:
    def list_with_source_context(self):
        return [
            {
                "requirement_key": "REQ-000001",
                "requirement_name": "短信登录",
                "final_requirement": "用户登录需要支持短信验证码",
                "status": "active",
                "source_types": ["web"],
                "departments": ["用户体验部"],
                "business_domains": ["auth"],
                "sensitivity_levels": ["internal"],
                "latest_source_submitted_at": "2026-09-07T10:00:00+08:00",
            },
            {
                "requirement_key": "REQ-000002",
                "requirement_name": "报表导出",
                "final_requirement": "运营报表支持导出",
                "status": "active",
                "source_types": ["email"],
                "departments": ["运营部"],
                "business_domains": ["report"],
                "sensitivity_levels": ["confidential"],
                "latest_source_submitted_at": "2026-09-06T10:00:00+08:00",
            },
        ]


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
    )

    result = service.search(
        "登录",
        filters={
            "channel": "web",
            "department": "用户体验部",
            "business_domain": "auth",
            "sensitivity_level": "internal",
            "submitted_from": "2026-09-07T00:00:00+08:00",
        },
    )

    assert [item["requirement_key"] for item in result] == ["REQ-000001"]
    assert result[0]["departments"] == ["用户体验部"]
    assert vector_repo.filters["department"] == "用户体验部"


def test_vector_repository_builds_metadata_filter_sql() -> None:
    where_clause, params = RequirementVectorRepository(dsn="postgresql://example")._build_metadata_filter(
        {
            "channel": "email",
            "department": "运营部",
            "business_domain": "report",
            "sensitivity_level": "confidential",
            "submitted_from": "2026-09-01T00:00:00+08:00",
            "submitted_to": "2026-09-08T00:00:00+08:00",
        }
    )

    assert "s2.source_type = %s" in where_clause
    assert "s4.submitted_at >= %s::timestamptz" in where_clause
    assert "s5.submitted_at <= %s::timestamptz" in where_clause
    assert params[0] == "email"
    assert "运营部" in params
    assert "report" in params
    assert "confidential" in params
