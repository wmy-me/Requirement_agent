from src.application.retrieval_service import RetrievalService


def test_retrieval_service_returns_candidates() -> None:
    service = RetrievalService()
    results = service.search("用户登录 验证码", limit=5)
    assert len(results) >= 1
    assert results[0]["requirement_key"] == "REQ-000001"
    assert results[0]["score"] >= 0.5
