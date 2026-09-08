"""决策子图：人工审核 → 落库节点（commit/reject）。

节点从 ctx（运行时注入的 repos + session + 审核入参）读写数据库；
事务边界（commit/rollback/close）由调用方 ReviewService 持有，节点内不 commit。
"""

from __future__ import annotations

from typing import Any, Literal

from src.domain.requirement import AuditEvent, RequirementMaster, RequirementReview, RequirementVersion
from src.graph.canonical import canonical_requirement, canonical_title


def _ctx(state: dict[str, Any]) -> dict[str, Any]:
    return state.get("ctx") or {}


def _source(state: dict[str, Any]) -> Any:
    ctx = _ctx(state)
    source = ctx.get("_source")
    if source is None:
        source = ctx["source_repo"].get_by_id(ctx["source_id"], session=ctx["session"])
        ctx["_source"] = source
    return source


def record_review_node(state: dict[str, Any]) -> dict[str, Any]:
    """校验来源状态并写入人工审核记录（顺序与旧 ReviewService 一致）。"""
    ctx = _ctx(state)
    session = ctx["session"]
    source_id = ctx["source_id"]

    source = ctx["source_repo"].get_by_id(source_id, session=session)
    if source is None:
        raise ValueError(f"source_id={source_id} not found")
    if source.processing_status != "pending_review":
        raise ValueError(f"source_id={source_id} is not pending review")
    ctx["_source"] = source

    review = RequirementReview(
        source_id=source_id,
        analysis_snapshot=ctx.get("analysis_snapshot") or dict(source.metadata.get("analysis") or {}),
        decision=ctx["decision"],
        reviewer_id=ctx["reviewer_id"],
        reviewer_name=ctx.get("reviewer_name"),
        review_comment=ctx.get("comment"),
        edited_requirement=ctx.get("edited_requirement"),
    )
    ctx["review_repo"].save(review, session=session)
    ctx["_review"] = review
    return {}


def route_decision(state: dict[str, Any]) -> Literal["commit", "reject"]:
    return "commit" if _ctx(state).get("decision") == "approved" else "reject"


def commit_requirement_node(state: dict[str, Any]) -> dict[str, Any]:
    """approved：allocate/get REQ → master → version → 溯源 → committed → 审计 → 出箱。"""
    ctx = _ctx(state)
    session = ctx["session"]
    source_id = ctx["source_id"]
    reviewer_id = ctx["reviewer_id"]
    comment = ctx.get("comment")
    edited_requirement = ctx.get("edited_requirement")
    decision = ctx["decision"]
    review: RequirementReview = ctx["_review"]
    source = _source(state)

    master: RequirementMaster | None = None
    target_key = ctx.get("requirement_key")
    if target_key:
        master = ctx["master_repo"].get_by_key(str(target_key), session=session)

    canonical_req_title = canonical_title(source, edited_requirement)
    canonical_req_text = canonical_requirement(source, edited_requirement)

    if master is None:
        master = RequirementMaster(
            requirement_key=ctx["master_repo"].allocate_key(session=session),
            requirement_name=canonical_req_title[:80],
            final_requirement=canonical_req_text,
            current_version=0,
            status="active",
            lock_version=0,
        )
        master = ctx["master_repo"].save(master, session=session)

    current_version = int(master.current_version or 0)
    next_version = current_version + 1
    version = RequirementVersion(
        requirement_id=int(master.id or 0),
        parent_version_id=None,
        version_no=next_version,
        version_title=canonical_req_title[:80],
        change_type="new" if current_version == 0 else "modify",
        requirement_snapshot=canonical_req_text,
        change_summary=comment or "审核通过并生成版本快照",
        diff_payload={
            "decision": decision,
            "reviewer_id": reviewer_id,
            "reviewer_name": ctx.get("reviewer_name"),
            "edited_requirement": edited_requirement,
            "source_id": source_id,
            "source_type": source.source_type,
            "source_event_id": source.source_event_id,
            "source_original_text": source.original_text,
            "source_extracted_text": source.extracted_text,
            "structured_extraction": source.metadata.get("extracted") or {},
            "standardized_document": source.metadata.get("standardized_document") or {},
            "analysis": source.metadata.get("analysis") or ctx.get("analysis_snapshot") or {},
            "risk": source.metadata.get("risk") or {},
        },
        created_by=reviewer_id,
        reviewed_by=reviewer_id,
    )
    saved_version = ctx["version_repo"].save(version, session=session)
    if saved_version.id is not None and source.id is not None:
        ctx["version_repo"].link_source(saved_version.id, source.id, session=session)

    master.requirement_name = canonical_req_title[:80]
    master.final_requirement = canonical_req_text
    master.current_version = next_version
    master.status = "active"
    master.lock_version = next_version
    ctx["master_repo"].save(master, session=session)

    trace_metadata = {
        **source.metadata,
        "requirement_key": master.requirement_key,
        "requirement_id": master.id,
        "version_id": saved_version.id,
        "version_no": saved_version.version_no,
        "trace": {
            "source_id": source_id,
            "requirement_key": master.requirement_key,
            "version_id": saved_version.id,
            "version_no": saved_version.version_no,
            "relation_type": "source",
        },
    }
    ctx["source_repo"].update_status(source_id, "committed", metadata=trace_metadata, session=session)
    ctx["audit_repo"].record(
        AuditEvent(
            trace_id=f"review-{source_id}-{reviewer_id}",
            event_type="requirement_review_approved",
            aggregate_type="requirement_master",
            aggregate_id=str(master.requirement_key),
            actor_type="reviewer",
            actor_id=reviewer_id,
            before_data={"source_id": source_id, "version": current_version},
            after_data={"version": next_version, "requirement_key": master.requirement_key},
            result_status="success",
        ),
        session=session,
    )
    ctx["outbox_repo"].enqueue(
        aggregate_type="requirement_master",
        aggregate_id=str(master.requirement_key),
        event_type="embedding_sync",
        payload={
            "requirement_id": master.id,
            "requirement_key": master.requirement_key,
            "version_id": saved_version.id,
            "version_no": saved_version.version_no,
            "content": master.final_requirement,
        },
        session=session,
    )
    return {
        "outcome": {
            "decision": review.decision,
            "reviewer_id": review.reviewer_id,
            "status": "recorded",
            "version_no": saved_version.version_no,
            "requirement_key": master.requirement_key,
        }
    }


def reject_requirement_node(state: dict[str, Any]) -> dict[str, Any]:
    """rejected/returned：来源置对应状态 + 审计。"""
    ctx = _ctx(state)
    session = ctx["session"]
    source_id = ctx["source_id"]
    decision = ctx["decision"]
    reviewer_id = ctx["reviewer_id"]
    review: RequirementReview = ctx["_review"]
    source = _source(state)

    ctx["source_repo"].update_status(source_id, decision, metadata=source.metadata, session=session)
    ctx["audit_repo"].record(
        AuditEvent(
            trace_id=f"review-{source_id}-{reviewer_id}",
            event_type=f"requirement_review_{decision}",
            aggregate_type="requirement_source",
            aggregate_id=str(source_id),
            actor_type="reviewer",
            actor_id=reviewer_id,
            before_data={"source_id": source_id},
            after_data={"decision": decision, "comment": ctx.get("comment")},
            result_status="success",
        ),
        session=session,
    )
    return {
        "outcome": {
            "decision": review.decision,
            "reviewer_id": review.reviewer_id,
            "status": "recorded",
            "version_no": None,
            "requirement_key": ctx.get("requirement_key"),
        }
    }
