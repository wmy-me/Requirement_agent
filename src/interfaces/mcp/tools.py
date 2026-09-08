"""Requirement Agent 对外暴露的 MCP 工具集（薄适配层）。

原则：工具只做“入参 → 领域服务/只读 repo”的转发，**绝不在此实现业务或直接写库**。
- 写类能力（需求提交、审核落库）一律走 `RequirementService` / `ReviewService`
  （内部经 LangGraph 分析与审核流程，含审计/outbox）。
- 外部 Agent 如需“新增正式需求”，请走 `submit_requirement`（进入待审），
  在人工审核通过后才会写入 requirement_master —— MCP 不提供绕过评审的直接写主表工具。
"""

from __future__ import annotations

from typing import Any

from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP

from src.config.settings import settings
from src.domain.requirement import RequirementSource
from src.interfaces.mcp.auth import StaticTokenVerifier
from src.application.requirement_service import RequirementService
from src.application.retrieval_service import RetrievalService
from src.application.review_service import ReviewService
from src.infrastructure.db.repositories import RequirementMasterRepository, RequirementVersionRepository

mcp = FastMCP(
    "requirement-agent",
    token_verifier=StaticTokenVerifier(),
    auth=AuthSettings(
        issuer_url=settings.mcp_issuer_url,
        resource_server_url=settings.mcp_resource_url,
        required_scopes=["mcp"],
    ),
)

requirement_service = RequirementService()
retrieval_service = RetrievalService()
review_service = ReviewService()
master_repo = RequirementMasterRepository()
version_repo = RequirementVersionRepository()


@mcp.tool()
def health_check() -> dict[str, str]:
    """MCP 服务健康检查。"""
    return {"status": "ok"}


@mcp.tool()
def submit_requirement(
    original_text: str,
    source_type: str = "web",
    requester_id: str | None = None,
    requester_name: str | None = None,
    source_event_id: str | None = None,
) -> dict[str, Any]:
    """提交一条需求进入治理流程（抽取 → 冲突/重复分析 → 风险评估 → 待人工审核）。

    不会直接写入正式需求主表；审核通过后才生成 REQ。返回值含 source_id / status / analysis / risk。
    """
    source = RequirementSource(
        idempotency_key=f"{source_type}:{(requester_id or 'mcp')}:{original_text}",
        source_type=source_type,
        source_event_id=(source_event_id or "").strip() or None,
        requester_id=(requester_id or "").strip() or None,
        requester_name=(requester_name or "").strip() or None,
        original_text=original_text,
        original_payload={"input_mode": "text", "source": "mcp"},
        metadata={},
    )
    return requirement_service.submit_requirement(source)


@mcp.tool()
def search_requirements(query: str, limit: int = 10) -> dict[str, Any]:
    """基于项目检索服务搜索需求记录（关键字 + 向量）。"""
    rows = retrieval_service.search(query, limit=limit)
    return {"query": query, "limit": limit, "results": rows}


@mcp.tool()
def submit_review_decision(
    source_id: int,
    decision: str,
    reviewer_name: str | None = None,
    comment: str | None = None,
    edited_requirement: str | None = None,
) -> dict[str, str]:
    """提交对某条需求来源的人工审核结果（approved / rejected / returned）。"""
    return review_service.submit_decision(
        source_id=source_id,
        decision=decision,
        reviewer_id=settings.mcp_actor_id,
        reviewer_name=reviewer_name,
        comment=comment,
        edited_requirement=edited_requirement,
    )


@mcp.tool()
def get_requirement_detail(requirement_id: int) -> dict[str, Any]:
    """根据主键 id 查询正式需求详情。"""
    item = master_repo.get_by_id(requirement_id)
    if item is None:
        return {"requirement_id": requirement_id, "status": "not_found"}
    return {
        "id": requirement_id,
        "requirement_key": item.requirement_key,
        "requirement_name": item.requirement_name,
        "final_requirement": item.final_requirement,
        "current_version": item.current_version,
        "status": item.status,
        "lock_version": item.lock_version,
    }


@mcp.tool()
def get_requirement_versions(requirement_id: int, limit: int = 20) -> dict[str, Any]:
    """返回某个正式需求的版本历史。"""
    master = master_repo.get_by_id(requirement_id)
    if master is None:
        return {"requirement_id": requirement_id, "status": "not_found"}
    rows = version_repo.list_by_requirement_key(master.requirement_key)
    return {
        "requirement_id": requirement_id,
        "requirement_key": master.requirement_key,
        "limit": limit,
        "results": rows[:limit],
    }


@mcp.tool()
def get_master_requirements(limit: int = 50, status: str = "active") -> dict[str, Any]:
    """返回符合状态条件的主需求列表（默认 active）。status 传空串返回全部。"""
    rows = master_repo.search("", limit=limit)
    filtered = [r for r in rows if r.get("status") == status] if status else rows
    return {"limit": limit, "status": status or None, "results": filtered}


__all__ = [
    "mcp",
    "health_check",
    "submit_requirement",
    "search_requirements",
    "submit_review_decision",
    "get_requirement_detail",
    "get_requirement_versions",
    "get_master_requirements",
]
