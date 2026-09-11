from requirement_agent.application.retrieval_service import RetrievalService


def test_retrieval_service_returns_candidates() -> None:
    service = RetrievalService()
    results = service.search("用户登录 验证码", limit=5)
    assert len(results) >= 1
    assert results[0]["requirement_key"] == "REQ-000001"
    assert results[0]["score"] >= 0.5


class FakeMasterRepo:
    def list_with_source_context(self):
        return [
            {
                "requirement_key": "REQ-K",
                "requirement_name": "报表导出功能",
                "final_requirement": "支持报表导出",
                "status": "active",
                "business_domains": ["general"],
                "feature_contents": [],
                "source_types": [],
                "requester_names": [],
                "departments": [],
                "sensitivity_levels": [],
                "current_version": 1,
                "feature_count": 1,
            }
        ]


class FakeEmbeddingService:
    def embed(self, text: str) -> list[float]:
        return [0.1] * 4096

    def is_configured(self) -> bool:
        return True


class FakeVectorRepo:
    def search(self, query_vector, limit=10, filters=None):
        # REQ-K：与关键词候选重合但向量分更高；REQ-NEW：纯向量命中
        return [
            {
                "requirement_key": "REQ-K", "title": "报表导出功能", "summary": "支持报表导出",
                "score": 0.9, "business_domain": "general", "status": "active",
                "source_types": [], "departments": [], "sensitivity_levels": [],
            },
            {
                "requirement_key": "REQ-NEW", "title": "数据分析面板", "summary": "数据分析面板",
                "score": 0.85, "business_domain": "general", "status": "active",
                "source_types": [], "departments": [], "sensitivity_levels": [],
            },
        ]


def test_vector_score_overrides_and_appends_in_merge() -> None:
    """向量分高于关键词分时应覆盖排序；纯向量命中的候选应被追加进结果。"""
    service = RetrievalService(
        vector_repo=FakeVectorRepo(),
        embedding_service=FakeEmbeddingService(),
        master_repo=FakeMasterRepo(),
    )
    results = service.search("报表 天气", limit=5)
    assert [r["requirement_key"] for r in results] == ["REQ-K", "REQ-NEW"]
    # 已有关键词候选的 REQ-K 被向量分覆盖
    assert results[0]["score"] == 0.9
    assert results[0]["match_type"] == "vector"
    # 纯向量命中的 REQ-NEW 被追加
    assert results[1]["score"] == 0.85
    assert results[1]["match_type"] == "vector"
