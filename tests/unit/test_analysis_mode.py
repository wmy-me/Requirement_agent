"""校验 analysis_mode 接线：模式需真实改变判定阈值与展示标签（此前是被接受但未使用的死参数）。"""

import pytest

from requirement_agent.agents.analyze_agent import AnalyzeAgent, score_label, thresholds_for
from requirement_agent.agents.extract_agent import ExtractedRequirement
from requirement_agent.skills.analyze_skill import AnalyzeSkill


class UnconfiguredProvider:
    """未配置 LLM 的 provider，强制走启发式分支，使结论只由阈值决定。"""

    def is_configured(self) -> bool:
        return False


def _extracted() -> ExtractedRequirement:
    return ExtractedRequirement(
        requirement_title="登录增强",
        summary="支持短信验证码登录",
        business_domain="auth",
        tags=["登录"],
        requirements=["短信验证码登录"],
        raw_text="支持短信验证码登录。",
    )


HISTORY = [
    {
        "requirement_key": "REQ-000001",
        "requirement_name": "短信验证码登录",
        "final_requirement": "支持短信验证码登录",
    }
]


def _agent_at(similarity: float, monkeypatch: pytest.MonkeyPatch) -> AnalyzeAgent:
    """把相似度固定为给定值，从而只观察阈值本身如何决定结论。"""
    monkeypatch.setattr(
        AnalyzeAgent,
        "_score_similarity",
        lambda self, extracted, title, summary: (similarity, []),
    )
    return AnalyzeAgent(skill=AnalyzeSkill(provider=UnconfiguredProvider()))


def test_strict_mode_keeps_historical_defaults() -> None:
    assert thresholds_for(None) == {"duplicate": 0.70, "related": 0.45, "candidate": 0.35}
    assert thresholds_for("strict") == thresholds_for(None)


@pytest.mark.parametrize("mode", ["balanced", "broad"])
def test_modes_relax_thresholds_monotonically(mode: str) -> None:
    strict = thresholds_for("strict")
    loosened = thresholds_for(mode)
    assert loosened["duplicate"] <= strict["duplicate"]
    assert loosened["related"] <= strict["related"]
    assert loosened["candidate"] <= strict["candidate"]


def test_unknown_mode_falls_back_to_strict() -> None:
    assert thresholds_for("激进") == thresholds_for("strict")


def test_mode_changes_verdict_for_same_similarity(monkeypatch: pytest.MonkeyPatch) -> None:
    # 同一相似度 0.55：strict 下只是「关联」，broad 下已算「重复」
    strict = _agent_at(0.55, monkeypatch).analyze(_extracted(), HISTORY, analysis_mode="strict")
    assert strict.duplicate is False
    assert strict.related is True

    broad = _agent_at(0.55, monkeypatch).analyze(_extracted(), HISTORY, analysis_mode="broad")
    assert broad.duplicate is True
    assert broad.independent is False


def test_below_candidate_threshold_stays_independent_in_every_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _agent_at(0.10, monkeypatch).analyze(_extracted(), HISTORY, analysis_mode="broad")
    assert result.candidates == []
    assert result.independent is True


def test_score_label_follows_mode_thresholds() -> None:
    assert score_label(0.55, thresholds_for("strict")) == "中"
    assert score_label(0.55, thresholds_for("broad")) == "高"
    assert score_label(0.10, thresholds_for("broad")) == "低"
