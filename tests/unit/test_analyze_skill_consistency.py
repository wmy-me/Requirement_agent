"""校验 AnalyzeSkill 对 LLM 布尔字段的交叉修正：消除 duplicate∧independent 等自相矛盾。"""

from requirement_agent.agents.extract_agent import ExtractedRequirement
from requirement_agent.skills.analyze_skill import AnalyzeSkill


class FakeProvider:
    configured = True

    def __init__(self, text: str) -> None:
        self.text = text

    def is_configured(self) -> bool:
        return True

    def generate(self, prompt: str, system_prompt: str | None = None) -> str:
        return self.text


def _extracted() -> ExtractedRequirement:
    return ExtractedRequirement(
        requirement_title="登录增强",
        summary="支持短信验证码登录",
        business_domain="auth",
        tags=["登录"],
        requirements=["短信验证码登录"],
        raw_text="支持短信验证码登录。",
    )


def test_hallucinated_duplicate_without_candidate_is_corrected() -> None:
    # LLM 谎报 duplicate=true 且 independent=true，但没有相似候选
    provider = FakeProvider(
        '{"duplicate": true, "related": false, "conflict": false, "independent": true, '
        '"reasoning": "自相矛盾", "candidates": []}'
    )
    result = AnalyzeSkill(provider=provider).analyze(_extracted(), historical_requirements=[])

    assert result.duplicate is False  # 无 ≥0.8 候选佐证 → 取消假阳性
    assert result.independent is True  # 重算为独立
    assert result.independent == (not (result.duplicate or result.related or result.conflict))


def test_underreported_duplicate_is_not_forced_by_similarity() -> None:
    """LLM 说不是重复时，高分候选**不再**把结论翻转成重复。

    ⚠️ 行为反转：本用例原先断言「漏报会被候选分数补回 duplicate=true」。该规则的前提是
    similarity 为模型独立判断，实测它只是检索分数的原样回显，于是等价于「向量分数高就
    直接判重复」，会推翻模型理由、产出「重复=是」配「理由：非重复」的矛盾卡片。
    现在改为：不翻转，但保留「关联」标记提示人工核对——漏报的代价远小于自相矛盾。
    """
    provider = FakeProvider(
        '{"duplicate": false, "related": false, "conflict": false, "independent": true, '
        '"reasoning": "看走眼", "candidates": [{"requirement_key": "REQ-000001", '
        '"title": "短信登录", "similarity": 0.9, "reason": "高度相似"}]}'
    )
    result = AnalyzeSkill(provider=provider).analyze(_extracted(), historical_requirements=[])

    assert result.duplicate is False  # 不再被候选分数翻转
    assert result.related is True  # 但会标记关联，提示人工核对
    assert result.independent is False


def test_confirmed_duplicate_with_candidate_is_kept() -> None:
    provider = FakeProvider(
        '{"duplicate": true, "related": true, "conflict": false, "independent": false, '
        '"reasoning": "重复", "candidates": [{"requirement_key": "REQ-000001", '
        '"title": "短信登录", "similarity": 0.95, "reason": "重复"}]}'
    )
    result = AnalyzeSkill(provider=provider).analyze(_extracted(), historical_requirements=[])

    assert result.duplicate is True
    assert result.independent is False
    assert result.candidates[0].similarity == 0.95


def test_high_similarity_does_not_force_duplicate() -> None:
    """候选相似度再高，也**不得**把 LLM 的 duplicate=false 翻转成 true。

    那条「达到阈值就补判重复」的规则，前提是 similarity 为模型的独立判断；但实测它只是
    把检索分数原样回显（0.7313209960078035 逐位相同），于是该规则等价于「向量分数高就
    直接判重复」，会推翻模型结论并产出「重复=是」配「理由：非重复」的矛盾卡片。
    """
    provider = FakeProvider(
        '{"duplicate": false, "related": false, "conflict": false, "independent": true, '
        '"reasoning": "仅存在弱关联，非重复、非冲突", "candidates": [{"requirement_key": "REQ-000001", '
        '"title": "后台报表支持按部门筛选导出", "similarity": 0.95, "reason": "弱关联"}]}'
    )
    result = AnalyzeSkill(provider=provider).analyze(_extracted(), historical_requirements=[])

    assert result.duplicate is False  # 不再被翻转
    assert result.related is True  # 但高相似候选仍算「关联」


def test_parse_failure_falls_back_to_heuristic() -> None:
    provider = FakeProvider("不是 JSON 的一堆话")
    result = AnalyzeSkill(provider=provider).analyze(_extracted(), historical_requirements=[])
    # heuristic：无历史 → independent=True、duplicate=False
    assert result.duplicate is False
    assert result.independent is True
