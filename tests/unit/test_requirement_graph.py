from src.agents.analyze_agent import AnalysisResult
from src.agents.risk_agent import RiskAssessment
from src.graph.requirement_graph import RequirementGraph


def test_requirement_graph_commits() -> None:
    graph = RequirementGraph()
    state = graph.run({
        "source_text": "用户登录需要支持手机号登录、短信验证码和权限校验。",
        "source_type": "web",
        "requester_name": "alice",
    })
    assert state.status == "committed"
    assert state.requirement_key


def test_requirement_graph_routes_to_review_when_duplicate_detected(monkeypatch) -> None:
    def fake_analyze(self, extracted, historical_requirements=None):
        return AnalysisResult(
            duplicate=True,
            related=False,
            conflict=False,
            independent=False,
            reasoning="需求与历史记录高度相似，建议修订。",
            candidates=[],
        )

    def fake_assess(self, extracted):
        return RiskAssessment(
            quality_risk="low",
            change_risk="low",
            technical_impact_risk="low",
            confidence=0.9,
        )

    monkeypatch.setattr("src.agents.analyze_agent.AnalyzeAgent.analyze", fake_analyze)
    monkeypatch.setattr("src.agents.risk_agent.RiskAgent.assess", fake_assess)

    state = RequirementGraph().run({
        "source_text": "用户登录需要支持手机号登录。",
        "source_type": "web",
        "requester_name": "bob",
    })

    assert state.status == "needs_revision"
    assert state.review_decision == "needs_revision"
