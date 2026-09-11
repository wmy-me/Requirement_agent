"""校验 AnalyzeSkill 对 LLM 布尔字段的交叉修正：消除 duplicate∧independent 等自相矛盾。"""

from src.requirement_agent.agents.extract_agent import ExtractedRequirement
from src.requirement_agent.skills.analyze_skill import AnalyzeSkill


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


def test_underreported_duplicate_is_restored_from_candidate() -> None:
    # LLM 漏报 duplicate=false，但有 0.9 相似候选
    provider = FakeProvider(
        '{"duplicate": false, "related": false, "conflict": false, "independent": true, '
        '"reasoning": "看走眼", "candidates": [{"requirement_key": "REQ-000001", '
        '"title": "短信登录", "similarity": 0.9, "reason": "高度相似"}]}'
    )
    result = AnalyzeSkill(provider=provider).analyze(_extracted(), historical_requirements=[])

    assert result.duplicate is True
    assert result.related is True
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


def test_parse_failure_falls_back_to_heuristic() -> None:
    provider = FakeProvider("不是 JSON 的一堆话")
    result = AnalyzeSkill(provider=provider).analyze(_extracted(), historical_requirements=[])
    # heuristic：无历史 → independent=True、duplicate=False
    assert result.duplicate is False
    assert result.independent is True
