"""Isolated-schema wiring checks for the legacy-agent tool adapters.

The test owns no business fixtures and does not call a model.  It verifies the
same registry/invoker path used by the workflow nodes against a freshly migrated
database; extract/analyze/risk fall back to their deterministic implementations.
"""

from __future__ import annotations

import threading

from requirement_agent.tools.invoker import invoke
from requirement_agent.tools.registry import names
from requirement_agent.workflows.agents_nodes import analyze_node, extract_node, risk_node
from requirement_agent.application.agent_execution_service import AgentExecutionService


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


def test_execution_claim_and_terminal_updates_are_conditional() -> None:
    service = AgentExecutionService()
    run = service.start(task_type="isolated-test")
    run_id = str(run["run_id"])
    claimed = service.claim(run_id)
    assert claimed is not None
    token = str(claimed["execution_token"])
    assert service.claim(run_id) is None, "second executor must not acquire a running run"
    # Simulate a concurrent user cancellation before the stale executor returns.
    service.repo._update_run(run_id, status="cancelled")
    assert service.complete(run_id, token) is False
    assert service.repo.get_run(run_id)["status"] == "cancelled"


def test_expired_lease_reclaims_and_rejects_old_writer() -> None:
    service = AgentExecutionService()
    run_id = str(service.start(task_type="isolated-lease")["run_id"])
    first = service.claim(run_id)
    assert first is not None
    first_token = str(first["execution_token"])
    # Test-only forced expiry simulates a crashed worker without using business data.
    # SQL expression is test-only to simulate an expired executor lease.
    from requirement_agent.infrastructure.db.session import SessionLocal
    from sqlalchemy import text
    with SessionLocal() as session:
        session.execute(text("UPDATE agent_run SET lease_expires_at = NOW() - interval '1 second' WHERE run_id = CAST(:r AS UUID)"), {"r": run_id})
        session.commit()
    second = service.claim(run_id)
    assert second is not None
    second_token = str(second["execution_token"])
    assert second_token != first_token
    assert service.checkpoint(run_id, first_token, {"old": True}) is False
    assert service.complete(run_id, first_token) is False
    assert service.checkpoint(run_id, second_token, {"turn": 1, "remaining_tool_calls": 11}) is True
    assert service.events(run_id, first_token, [{"event_type": "late", "payload": {}}]) is False
    assert service.tool_calls(run_id, first_token, [{"tool": "late", "params": {}, "status": "success", "duration_ms": 1, "count": 0, "message": None, "sample": None}]) is False


def test_two_executors_race_on_one_claim_with_barrier() -> None:
    service = AgentExecutionService()
    run_id = str(service.start(task_type="claim-race")["run_id"])
    barrier = threading.Barrier(3)
    claimed: list[object] = []

    def contender() -> None:
        barrier.wait()
        claimed.append(AgentExecutionService().claim(run_id))

    threads = [threading.Thread(target=contender), threading.Thread(target=contender)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()
    assert sum(item is not None for item in claimed) == 1
