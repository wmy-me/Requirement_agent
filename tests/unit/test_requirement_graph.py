from requirement_agent.agents.analyze_agent import AnalysisResult
from requirement_agent.agents.extract_agent import ExtractedRequirement
from requirement_agent.agents.risk_agent import RiskAssessment
from requirement_agent.workflows import run_analysis


def _extracted(**overrides):
    base = dict(
        requirement_title="登录增强",
        summary="支持短信验证码登录",
        requester_name=None,
        source_type="web",
        business_domain="auth",
        priority="medium",
        tags=["登录"],
        requirements=["短信验证码登录", "记录审计"],
        raw_text="支持短信验证码登录并记录审计。",
    )
    base.update(overrides)
    return ExtractedRequirement(**base)


def test_analysis_graph_independent_can_commit_and_keeps_full_fields(monkeypatch) -> None:
    monkeypatch.setattr(
        "requirement_agent.agents.extract_agent.ExtractAgent.extract",
        lambda self, raw_text, **kw: _extracted(),
    )
    monkeypatch.setattr(
        "requirement_agent.agents.retrieval_agent.RetrievalAgent.retrieve",
        lambda self, query, limit=5: [],
    )
    monkeypatch.setattr(
        "requirement_agent.agents.analyze_agent.AnalyzeAgent.analyze",
        lambda self, extracted, historical=None: AnalysisResult(independent=True),
    )
    monkeypatch.setattr(
        "requirement_agent.agents.risk_agent.RiskAgent.assess",
        lambda self, extracted: RiskAssessment(change_risk="medium"),
    )

    state = run_analysis(
        source_text="支持短信验证码登录。",
        source_type="web",
        requester_name="alice",
    )

    assert state["decision"] == "can_commit"
    assert state["next_action"] == "can_commit"
    # 风险恒在决策前已算
    assert state["risk"]["change_risk"] == "medium"
    # 完整字段透传，未降级
    assert state["extracted"]["business_domain"] == "auth"
    assert state["extracted"]["requirements"] == ["短信验证码登录", "记录审计"]


def test_analysis_graph_duplicate_forces_manual_review_with_risk(monkeypatch) -> None:
    monkeypatch.setattr(
        "requirement_agent.agents.extract_agent.ExtractAgent.extract",
        lambda self, raw_text, **kw: _extracted(),
    )
    monkeypatch.setattr(
        "requirement_agent.agents.retrieval_agent.RetrievalAgent.retrieve",
        lambda self, query, limit=5: [{"requirement_key": "REQ-000001", "requirement_name": "短信登录"}],
    )
    monkeypatch.setattr(
        "requirement_agent.agents.analyze_agent.AnalyzeAgent.analyze",
        lambda self, extracted, historical=None: AnalysisResult(
            duplicate=True,
            related=True,
            conflict=False,
            independent=False,
            reasoning="与 REQ-000001 高度相似。",
            candidates=[],
        ),
    )
    monkeypatch.setattr(
        "requirement_agent.agents.risk_agent.RiskAgent.assess",
        lambda self, extracted: RiskAssessment(change_risk="high"),
    )

    state = run_analysis(source_text="支持短信验证码登录。")

    assert state["decision"] == "manual_review"
    # 命中 duplicate → 人工审核，且 risk 也被评估（不再是空 dict）
    assert state["risk"]["change_risk"] == "high"
    assert state["candidates"]


def test_analysis_graph_high_risk_triggers_manual_review(monkeypatch) -> None:
    monkeypatch.setattr(
        "requirement_agent.agents.extract_agent.ExtractAgent.extract",
        lambda self, raw_text, **kw: _extracted(),
    )
    monkeypatch.setattr(
        "requirement_agent.agents.retrieval_agent.RetrievalAgent.retrieve",
        lambda self, query, limit=5: [],
    )
    monkeypatch.setattr(
        "requirement_agent.agents.analyze_agent.AnalyzeAgent.analyze",
        lambda self, extracted, historical=None: AnalysisResult(independent=True),
    )
    monkeypatch.setattr(
        "requirement_agent.agents.risk_agent.RiskAgent.assess",
        lambda self, extracted: RiskAssessment(technical_impact_risk="high"),
    )

    state = run_analysis(source_text="对账系统变更，影响资金结算。")

    assert state["decision"] == "manual_review"
    assert state["risk"]["technical_impact_risk"] == "high"
