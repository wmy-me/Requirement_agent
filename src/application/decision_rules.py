"""需求“是否需要人工审核”的决策规则（全系统唯一来源）。

供分析图 decide 节点、HTTP /agent/run 与 RequirementService 复用，
避免三处重复的判定逻辑漂移。
"""

from __future__ import annotations

RISK_KEYS = ("quality_risk", "change_risk", "technical_impact_risk")


def review_required(analysis: dict[str, object], risk: dict[str, object]) -> bool:
    """命中重复/冲突，或存在中高风险且结论不够确定时需要人工审核。"""
    high_risk = any(risk.get(key) == "high" for key in RISK_KEYS)
    medium_risk = sum(1 for key in RISK_KEYS if risk.get(key) == "medium")
    related = bool(analysis.get("related"))
    return bool(
        analysis.get("duplicate")
        or analysis.get("conflict")
        or high_risk
        or (related and medium_risk >= 2)
    )


def next_action_for(analysis: dict[str, object], risk: dict[str, object]) -> str:
    """返回 manual_review | can_commit。"""
    return "manual_review" if review_required(analysis, risk) else "can_commit"
