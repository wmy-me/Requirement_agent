"""检索服务的打分与融合。

**B4 批 3 起 `score` 不再是「像相似度的 0~1 数字」，而是 RRF 名次分（量级 ~0.01–0.03）。**
这个文件同时守着那次改动的三件事：

1. 删掉了一条与查询无关的 `+0.2` 加成；
2. 用户提交加权从「既进分数又当排序键」改为只当排序键（此前是重复计数）；
3. 融合从「向量分高于关键词分就覆盖」改为 RRF —— 不再拿两个不同量纲的数比大小。
"""

from requirement_agent.application.retrieval_service import RetrievalService


def test_retrieval_service_returns_candidates() -> None:
    """打真实库的冒烟测试：能召回，且**余弦真的透出来了**。

    这里刻意不再断言 `score >= 0.5` —— `score` 现在是名次分。要看「像不像」请读
    `vector_similarity`。
    """
    service = RetrievalService()
    results = service.search("用户登录 验证码", limit=5)
    assert len(results) >= 1
    assert results[0]["requirement_key"] == "REQ-000001"
    cosine = results[0]["vector_similarity"]
    assert cosine is not None and 0.0 <= cosine <= 1.0


class FakeMasterRepo:
    def __init__(self, items: list[dict] | None = None) -> None:
        self._items = items if items is not None else [
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

    def list_with_source_context(self):
        return self._items


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


def _service(master_repo=None) -> RetrievalService:
    return RetrievalService(
        vector_repo=FakeVectorRepo(),
        embedding_service=FakeEmbeddingService(),
        master_repo=master_repo or FakeMasterRepo(),
    )


def test_two_lists_are_fused_by_rank_not_by_comparing_scores() -> None:
    """两条召回各自成榜，按**名次**融合，谁也不跟谁比大小。"""
    results = _service().search("报表 天气", limit=5)

    assert [r["requirement_key"] for r in results] == ["REQ-K", "REQ-NEW"]
    by_key = {r["requirement_key"]: r for r in results}

    # REQ-K 两条路都命中 → 两个名次各贡献一份 RRF
    assert by_key["REQ-K"]["match_type"] == "hybrid"
    assert by_key["REQ-K"]["keyword_score"] == 0.45
    assert by_key["REQ-K"]["vector_similarity"] == 0.9
    # REQ-NEW 只有向量一条路
    assert by_key["REQ-NEW"]["match_type"] == "vector"
    assert by_key["REQ-NEW"]["keyword_score"] is None
    assert by_key["REQ-NEW"]["vector_similarity"] == 0.85

    # 名次分：两榜第一是 1/61 + 1/61，单榜第二是 1/62
    assert by_key["REQ-K"]["retrieval_score"] > by_key["REQ-NEW"]["retrieval_score"]
    assert results[0]["score"] == results[0]["retrieval_score"], "score 是 retrieval_score 的别名"


def test_keyword_score_no_longer_gets_the_query_independent_bonus() -> None:
    """**回归**：那条与查询无关的 `+0.2` 没了。

    旧实现只要候选正文含 `["登录","权限","审批","报表","支付","导出","验证码"]` 之一
    就无条件 +0.2 —— 与查询毫无关系，而这七个词覆盖了绝大多数中文业务需求。
    本例的候选正文含「报表」，旧实现会得 0.65，现在只有 token 命中带来的 0.45。
    """
    results = _service().search("报表 天气", limit=5)
    assert results[0]["keyword_score"] == 0.45


def test_user_submitted_bonus_is_a_tiebreak_not_a_score_component() -> None:
    """**回归**：用户提交加权是**排序偏好**，不再同时进分数。

    此前它既往分里 `+0.35`、又在排序键里再来一次，是重复计数。
    两个除 key 外完全相同的候选，关键词分必须一致 —— 差别只体现在排序上。
    """

    def item(key: str) -> dict:
        return {
            "requirement_key": key,
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

    repo = FakeMasterRepo([item("REQ-000099"), item("REQ-999999")])
    results = _service(master_repo=repo).search("报表", limit=5)
    scores = {r["requirement_key"]: r["keyword_score"] for r in results}

    assert scores["REQ-000099"] == scores["REQ-999999"], "加权不该进分数"
    # 但排序上它在前（同为向量榜成员时，排序键里的加权生效）
    assert results[0]["requirement_key"] == "REQ-000099"
