"""动态 StateGraph 的安全路由与分支回归。"""

from requirement_agent.agents.analyze_agent import AnalysisResult
from requirement_agent.agents.extract_agent import ExtractedRequirement
from requirement_agent.agents.risk_agent import RiskAssessment
from requirement_agent.workflows.graphs import run_analysis
from requirement_agent.workflows.router import RouteProposal


def _extract() -> ExtractedRequirement:
    return ExtractedRequirement(requirement_title="导出", summary="按部门导出报表", raw_text="新增按部门导出报表", requirements=["按部门导出报表"])


def _patch_pipeline(monkeypatch, *, candidates=None, analysis=None):
    monkeypatch.setattr("requirement_agent.agents.extract_agent.ExtractAgent.extract", lambda *a, **k: _extract())
    from requirement_agent.tools.base import ToolResult
    monkeypatch.setattr("requirement_agent.workflows.agents_nodes.invoke", lambda *a, **k: ToolResult.empty("no candidates") if candidates is None else ToolResult.success(candidates))
    monkeypatch.setattr("requirement_agent.agents.analyze_agent.AnalyzeAgent.analyze", lambda *a, **k: analysis or AnalysisResult(independent=True))
    monkeypatch.setattr("requirement_agent.agents.risk_agent.RiskAgent.assess", lambda *a, **k: RiskAssessment())


def test_general_question_ends_without_requirement_artifacts() -> None:
    state = run_analysis(source_text="你能做什么？", intent=None, conversation_mode=True)
    assert state["intent"] == "general_question"
    assert state["answer"]
    assert "extracted" not in state
    assert [item["action"] for item in state["route_history"]] == ["answer"]


def test_short_requirement_clarifies_before_retrieval(monkeypatch) -> None:
    _patch_pipeline(monkeypatch)
    state = run_analysis(source_text="新增导出", conversation_mode=True)
    assert state["clarification_question"]
    assert "retrieve" not in state.get("completed_steps", [])
    assert state["route_history"][-1]["action"] == "clarify"


def test_empty_retrieval_still_reaches_review(monkeypatch) -> None:
    _patch_pipeline(monkeypatch, candidates=[])
    state = run_analysis(source_text="新增按部门导出报表，验收时应支持 XLSX。")
    assert state["candidates"] == []
    assert state["decision"] == "can_commit"
    assert {"extract", "retrieve", "analyze", "risk", "prepare_review"}.issubset(state["completed_steps"])


def test_illegal_model_action_falls_back_to_safe_plan(monkeypatch) -> None:
    _patch_pipeline(monkeypatch)
    monkeypatch.setattr("requirement_agent.workflows.router.settings.model_routes", {"router": {"provider": "deepseek", "model": "x"}})
    monkeypatch.setattr("requirement_agent.workflows.router._model_proposal", lambda state: RouteProposal(intent="requirement", action="prepare_review", reason="跳过所有步骤"))
    state = run_analysis(source_text="新增按部门导出报表，验收时应支持 XLSX。")
    assert state["route_history"][0]["action"] == "extract"
    assert state["error_state"]["code"] == "invalid_router_output"


def test_route_limit_stops_loop(monkeypatch) -> None:
    from requirement_agent.workflows.router import router_node
    state = router_node({"source_text": "新增按部门导出报表", "route_count": 12, "max_routes": 12})
    assert state["error_state"]["code"] == "route_limit"
    assert state["route_count"] == 13
