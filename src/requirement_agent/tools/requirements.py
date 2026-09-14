"""需求相关工具（提交 / 检索 / 详情 / 版本 / 主需求列表）。

原则：只做「入参 → 领域服务 / 只读 repo」的转发，不实现业务、不直接写库。
- 提交走 `RequirementService`（经 LangGraph 分析，进入待审），不直接写主表。
"""

from __future__ import annotations

from typing import Any

from requirement_agent.domain.requirement import RequirementSource
from requirement_agent.infrastructure.channels.base import InboundRequirement
from requirement_agent.tools._deps import (
    channel_ingest_service,
    master_repo,
    requirement_service,
    retrieval_service,
    version_repo,
)


def ingest_channel_event(
    channel: str,
    text: str,
    event_id: str | None = None,
    requester_id: str | None = None,
    requester_name: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """接入一条渠道事件：落库 + 排队分析，**不等分析结果**。

    与 `submit_requirement` 的区别：本方法立即返回（渠道要求在数秒内应答），分析由后台的
    `RequirementAnalysisTask` 补上 —— 来源会先停在 `received`，被消费后推进到 `pending_review`。

    幂等：同一 (channel, event_id) 重复投递会被去重，返回既有来源。
    """
    inbound = InboundRequirement(
        channel=channel,
        text=text,
        event_id=(event_id or "").strip() or None,
        requester_id=(requester_id or "").strip() or None,
        requester_name=(requester_name or "").strip() or None,
        payload=payload or {},
    )
    return channel_ingest_service.ingest(inbound)


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
        idempotency_key=f"{source_type}:{(requester_id or 'tool')}:{original_text}",
        source_type=source_type,
        source_event_id=(source_event_id or "").strip() or None,
        requester_id=(requester_id or "").strip() or None,
        requester_name=(requester_name or "").strip() or None,
        original_text=original_text,
        original_payload={"input_mode": "text", "source": "tool"},
        metadata={},
    )
    return requirement_service.submit_requirement(source)


def search_requirements(query: str, limit: int = 10) -> dict[str, Any]:
    """基于项目检索服务搜索需求记录（关键字 + 向量）。"""
    rows = retrieval_service.search(query, limit=limit)
    return {"query": query, "limit": limit, "results": rows}


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


def get_master_requirements(limit: int = 50, status: str = "active") -> dict[str, Any]:
    """返回符合状态条件的主需求列表（默认 active）。status 传空串返回全部。"""
    rows = master_repo.search("", limit=limit)
    filtered = [r for r in rows if r.get("status") == status] if status else rows
    return {"limit": limit, "status": status or None, "results": filtered}


__all__ = [
    "submit_requirement",
    "search_requirements",
    "get_requirement_detail",
    "get_requirement_versions",
    "get_master_requirements",
]
