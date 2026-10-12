from requirement_agent.agents.analyze_agent import AnalysisResult
from requirement_agent.agents.extract_agent import ExtractedRequirement
from requirement_agent.agents.risk_agent import RiskAssessment
from requirement_agent.workflows import run_analysis


def _wire_registered_tools(monkeypatch) -> None:
    """让图测试经过真实注册表边界，同时保留各 Agent 的确定性替身。"""
    from requirement_agent.agents.analyze_agent import AnalyzeAgent
    from requirement_agent.agents.extract_agent import ExtractAgent
    from requirement_agent.agents.retrieval_agent import RetrievalAgent
    from requirement_agent.agents.risk_agent import RiskAgent
    from requirement_agent.tools.base import ToolResult

    def invoke(name, params, **_kwargs):
        if name == "extract_requirement":
            return ToolResult.success(
                ExtractAgent().extract(
                    params["text"], source_type=params.get("source_type", "web"),
                    requester_name=params.get("requester_name"),
                ).model_dump(mode="python")
            )
        if name == "search_requirements":
            return ToolResult.success(RetrievalAgent().retrieve(params["query"], limit=params["limit"]))
        if name == "analyze_requirement_relation":
            extracted = ExtractedRequirement.model_validate(params["extracted"])
            return ToolResult.success(
                AnalyzeAgent().analyze(
                    extracted, params.get("historical_requirements"),
                    analysis_mode=params.get("analysis_mode", "strict"),
                ).model_dump(mode="python")
            )
        if name == "assess_requirement_risk":
            return ToolResult.success(
                RiskAgent().assess(ExtractedRequirement.model_validate(params["extracted"])).model_dump(mode="python")
            )
        raise AssertionError(f"unexpected tool: {name}")

    monkeypatch.setattr("requirement_agent.workflows.agents_nodes.invoke", invoke)


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
    _wire_registered_tools(monkeypatch)
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
        lambda self, extracted, historical=None, analysis_mode="strict": AnalysisResult(independent=True),
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
    _wire_registered_tools(monkeypatch)
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
        lambda self, extracted, historical=None, analysis_mode="strict": AnalysisResult(
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
    _wire_registered_tools(monkeypatch)
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
        lambda self, extracted, historical=None, analysis_mode="strict": AnalysisResult(independent=True),
    )
    monkeypatch.setattr(
        "requirement_agent.agents.risk_agent.RiskAgent.assess",
        lambda self, extracted: RiskAssessment(technical_impact_risk="high"),
    )

    state = run_analysis(source_text="对账系统变更，影响资金结算。")

    assert state["decision"] == "manual_review"
    assert state["risk"]["technical_impact_risk"] == "high"
