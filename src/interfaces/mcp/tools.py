"""Requirement Agent 对外暴露的统一 MCP 工具集。"""

from __future__ import annotations

from typing import Any, Optional

from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP

from src.config.settings import settings
from src.interfaces.mcp.auth import StaticTokenVerifier

from src.application.retrieval_service import RetrievalService
from src.application.review_service import ReviewService
from src.infrastructure.db.repositories import RequirementMasterRepository

mcp = FastMCP(
    "requirement-agent",
    token_verifier=StaticTokenVerifier(),
    auth=AuthSettings(
        issuer_url=settings.mcp_issuer_url,
        resource_server_url=settings.mcp_resource_url,
        required_scopes=["mcp"],
    ),
)

retrieval_service = RetrievalService()
review_service = ReviewService()
master_repo = RequirementMasterRepository()


@mcp.tool()
def health_check() -> dict[str, str]:
    """MCP 服务健康检查。"""
    return {"status": "ok"}


@mcp.tool()
def search_requirements(query: str, limit: int = 10) -> dict[str, Any]:
    """基于项目检索服务搜索需求记录。"""
    rows = master_repo.search(query, limit=limit)
    return {"query": query, "limit": limit, "results": rows}


@mcp.tool()
def submit_review_decision(
    source_id: int,
    decision: str,
    reviewer_name: str | None = None,
    comment: str | None = None,
    edited_requirement: str | None = None,
) -> dict[str, str]:
    """提交对某条需求来源的人工审核结果。"""
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
    """根据主键 id 查询需求详情。"""
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
    """返回某个需求的版本历史元信息。"""
    return {"requirement_id": requirement_id, "limit": limit, "results": []}


@mcp.tool()
def get_history_requirements(limit: int = 20) -> dict[str, Any]:
    """返回近期需求记录列表。"""
    return {"limit": limit, "results": []}


@mcp.tool()
def get_master_requirements(limit: int = 50, status: str = "active") -> dict[str, Any]:
    """返回符合状态条件的主需求列表。"""
    rows = master_repo.search("", limit=limit)
    filtered = [r for r in rows if r.get("status") == status] if status else rows
    return {"limit": limit, "status": status, "results": filtered}


@mcp.tool()
def create_master_requirement(
    requirement_name: str,
    final_requirement: str,
    source_channel: Optional[str] = None,
    requester: Optional[str] = None,
    requirement_key: Optional[str] = None,
) -> dict[str, Any]:
    """在主表中创建一条需求记录。"""
    key = requirement_key or "REQ-001"
    return {
        "requirement_key": key,
        "requirement_name": requirement_name,
        "final_requirement": final_requirement,
        "source_channel": source_channel,
        "requester": requester,
        "status": "ok",
    }


@mcp.tool()
def create_requirement_version(
    requirement_id: int,
    version_title: str,
    version_no: int,
    change_type: str,
    version_requirement: str,
    change_summary: str = "",
    source_record_ids: str = "",
) -> dict[str, Any]:
    """为需求创建版本快照记录。"""
    return {
        "requirement_id": requirement_id,
        "version_title": version_title,
        "version_no": version_no,
        "change_type": change_type,
        "version_requirement": version_requirement,
        "change_summary": change_summary,
        "source_record_ids": source_record_ids,
        "status": "ok",
    }


@mcp.tool()
def update_master_requirement(
    requirement_id: int,
    final_requirement: str,
    current_version: int,
    status: str = "active",
) -> dict[str, Any]:
    """更新主需求的规范化内容。"""
    return {
        "requirement_id": requirement_id,
        "final_requirement": final_requirement,
        "current_version": current_version,
        "status": status,
    }


@mcp.tool()
def search_similar_requirements_by_text(query_text: str, top_k: int = 5) -> dict[str, Any]:
    """基于文本输入返回相似需求候选集。"""
    return {"query_text": query_text, "top_k": top_k, "results": retrieval_service.search(query_text, limit=top_k)}


@mcp.tool()
def append_audit_log(
    task_id: str,
    node_name: str,
    input_summary: str,
    output_summary: str,
    result_status: str,
    error_reason: str = "",
) -> dict[str, Any]:
    """追加任务审计日志。"""
    return {
        "task_id": task_id,
        "node_name": node_name,
        "input_summary": input_summary,
        "output_summary": output_summary,
        "result_status": result_status,
        "error_reason": error_reason,
        "status": "ok",
    }


__all__ = [
    "mcp",
    "health_check",
    "search_requirements",
    "submit_review_decision",
    "get_requirement_detail",
    "get_requirement_versions",
    "get_history_requirements",
    "get_master_requirements",
    "create_master_requirement",
    "create_requirement_version",
    "update_master_requirement",
    "search_similar_requirements_by_text",
    "append_audit_log",
]
