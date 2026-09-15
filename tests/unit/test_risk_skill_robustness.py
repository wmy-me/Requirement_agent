"""RiskSkill 对非数值置信度的容错。

实测模型会把 `confidence` 写成 `"low"` 这类词。此前 `float("low")` 抛错会让整个
`RiskAssessment(...)` 构造失败 —— 三个本来有效的风险等级也一起被启发式覆盖。
与抽取技能的对象数组问题同族：一个字段的格式偏差不该拖垮整份结果。
"""

from requirement_agent.agents.extract_agent import ExtractedRequirement
from requirement_agent.skills.risk_skill import RiskSkill


class FakeProvider:
    configured = True

    def __init__(self, text: str) -> None:
        self.text = text

    def is_configured(self) -> bool:
        return True

    def generate(self, prompt: str, system_prompt: str | None = None) -> str:
        return self.text


def _extracted() -> ExtractedRequirement:
    """单条子需求 + medium 优先级 —— 启发式对它的质量风险只会给 low。"""
    return ExtractedRequirement(
        requirement_title="登录增强",
        summary="支持短信验证码登录",
        business_domain="auth",
        tags=["登录"],
        requirements=["短信验证码登录"],
        raw_text="支持短信验证码登录。",
    )


def test_non_numeric_confidence_only_falls_back_that_field() -> None:
    provider = FakeProvider(
        '{"quality_risk": "high", "change_risk": "medium", "technical_impact_risk": "low", '
        '"confidence": "low", "reasoning": "看到了风险"}'
    )

    result = RiskSkill(provider=provider).assess(_extracted())

    # 关键：模型给的三个风险等级必须保住（启发式对这份输入只会给 low）
    assert result.quality_risk == "high"
    assert result.change_risk == "medium"
    assert result.technical_impact_risk == "low"
    # 只有置信度退回兜底值，且仍在安全区间
    assert 0.0 <= result.confidence <= 0.95


def test_numeric_confidence_is_clamped_to_safe_range() -> None:
    provider = FakeProvider(
        '{"quality_risk": "low", "change_risk": "low", "technical_impact_risk": "low", '
        '"confidence": 1.8, "reasoning": "一切正常"}'
    )

    result = RiskSkill(provider=provider).assess(_extracted())

    assert result.confidence == 0.95  # 上限 0.95，避免 100% 的绝对表达
