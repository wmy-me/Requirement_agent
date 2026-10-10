"""Isolated-schema wiring checks for the legacy-agent tool adapters.

The test owns no business fixtures and does not call a model.  It verifies the
same registry/invoker path used by the workflow nodes against a freshly migrated
database; extract/analyze/risk fall back to their deterministic implementations.
"""

from __future__ import annotations

from requirement_agent.tools.invoker import invoke
from requirement_agent.tools.registry import names
from requirement_agent.workflows.agents_nodes import analyze_node, extract_node, risk_node


def test_legacy_analysis_adapters_are_registered_and_executable() -> None:
    assert {"extract_requirement", "analyze_requirement_relation", "assess_requirement_risk"} <= set(names())

    extracted = invoke("extract_requirement", {"text": "新增登录验证码，并记录登录失败原因。"}, consumer="analysis")
    assert extracted.status.value == "success"
    assert extracted.result["raw_text"]

    relation = invoke(
        "analyze_requirement_relation",
        {"extracted": extracted.result, "historical_requirements": []},
        consumer="analysis",
    )
    risk = invoke("assess_requirement_risk", {"extracted": extracted.result}, consumer="analysis")
    assert relation.status.value == "success"
    assert relation.result["independent"] is True
    assert risk.status.value == "success"
    assert risk.result["degraded"] is True


def test_workflow_nodes_use_registered_legacy_adapters() -> None:
    state = {"source_text": "新增登录验证码", "source_type": "web", "requester_name": "isolated-test"}
    extracted = extract_node(state)
    state.update(extracted)
    analyzed = analyze_node({**state, "candidates": []})
    assessed = risk_node(state)
    assert extracted["tool_calls"][0]["tool"] == "extract_requirement"
    assert analyzed["tool_calls"][0]["tool"] == "analyze_requirement_relation"
    assert assessed["tool_calls"][0]["tool"] == "assess_requirement_risk"
