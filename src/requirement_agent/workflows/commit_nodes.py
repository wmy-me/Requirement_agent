"""决策子图：人工审核 → 落库节点（commit/reject）。

节点从 ctx（运行时注入的 repos + session + 审核入参）读写数据库；
事务边界（commit/rollback/close）由调用方 ReviewService 持有，节点内不 commit。
"""

from __future__ import annotations

from typing import Any, Literal

from requirement_agent.agents.analyze_agent import thresholds_for
from requirement_agent.domain.requirement import AuditEvent, RequirementMaster, RequirementReview, RequirementVersion
from requirement_agent.workflows.canonical import canonical_requirement, canonical_title


def _ctx(state: dict[str, Any]) -> dict[str, Any]:
    """从决策图状态中取运行时上下文。"""
    return state.get("ctx") or {}


def _analysis_relations(
    analysis: dict[str, Any],
    *,
    master_repo: Any,
    session: Any,
    exclude_key: str,
) -> list[dict[str, object]]:
    """把分析结果里的候选翻译成待写入的需求关系边。

    三条规则：
    1. **只在对应布尔量为真时才写**：`duplicate` → `duplicates_of`、`related` → `related`、
       `conflict` → `conflict`。分析判为独立时不产生任何关系。
    2. **按该候选自己的相似度过阈值**：整份分析的布尔量是「至少有一个候选命中」，
       不能据此把每个候选都标上关系——否则一个 0.62 的弱候选也会被写成「关联」。
    3. **候选的 requirement_key 必须查得到对应 REQ**：检索候选里混着尚未成为正式需求
       （还在待审）的条目，写进去就是悬空关系。

    阈值取 strict（默认模式）——`analysis_mode` 目前不随分析结果落库，无从还原，
    取默认值是保守且可解释的选择。
    """
    thresholds = thresholds_for(None)
    relations: list[dict[str, object]] = []
    for candidate in analysis.get("candidates") or []:
        target_key = str(candidate.get("requirement_key") or "").strip()
        if not target_key or target_key == exclude_key:
            continue
        target = master_repo.get_by_key(target_key, session=session)
        if target is None or target.id is None:
            continue
        try:
            similarity = float(candidate.get("similarity") or 0.0)
        except (TypeError, ValueError):
            similarity = 0.0

        relation_type: str | None = None
        if analysis.get("duplicate") and similarity >= thresholds["duplicate"]:
            relation_type = "duplicates_of"
        elif analysis.get("related") and similarity >= thresholds["related"]:
            relation_type = "related"
        if relation_type is not None:
            relations.append(
                {
                    "target_requirement_id": target.id,
                    "target_requirement_key": target_key,
                    "relation_type": relation_type,
                    "reason": candidate.get("reason"),
                    "similarity": similarity,
                }
            )
        # 冲突不是从相似度推出来的（是标签启发式），单独记一条，与上面互不排斥
        if analysis.get("conflict"):
            relations.append(
                {
                    "target_requirement_id": target.id,
                    "target_requirement_key": target_key,
                    "relation_type": "conflict",
                    "reason": candidate.get("reason"),
                    "similarity": similarity,
                }
            )
    return relations


def _source(state: dict[str, Any]) -> Any:
    """按需加载 source，并在单次决策图内缓存，避免重复查库。"""
    ctx = _ctx(state)
    source = ctx.get("_source")
    if source is None:
        source = ctx["source_repo"].get_by_id(ctx["source_id"], session=ctx["session"])
        ctx["_source"] = source
    return source


def record_review_node(state: dict[str, Any]) -> dict[str, Any]:
    """校验来源状态并写入人工审核记录。

    只有 `pending_review` 的来源允许继续向后走；
    这里只记录审核动作，不做正式版本落库。
    """
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
    """approved 走 commit，其余审核结果统一走 reject 分支。"""
    return "commit" if _ctx(state).get("decision") == "approved" else "reject"


def _feature_candidates(source: Any, edited_requirement: str | None) -> list[str]:
    """把来源文本转成版本治理使用的 feature 候选行。

    优先使用人工改写结果，其次用结构化 `requirements[]`；
    兜底时按原文逐行拆分，保证版本功能集始终可落库。
    """
    edited = (edited_requirement or "").strip()
    if edited:
        return [line.strip() for line in edited.splitlines() if line.strip()]
    extracted = source.metadata.get("extracted") if isinstance(source.metadata, dict) else None
    if isinstance(extracted, dict):
        requirements = extracted.get("requirements")
        if isinstance(requirements, list):
            normalized = [str(item).strip() for item in requirements if str(item).strip()]
            if normalized:
                return normalized
    fallback = (source.extracted_text or source.original_text or "").strip()
    return [line.strip() for line in fallback.splitlines() if line.strip()]


def _module_lines(source: Any) -> list[dict[str, object]]:
    """若抽取结果带 modules，把它展平成携带模块标签的 feature 行。

    行结构 `{"content": "…", "module_key": "…", "module_name": "…"}`；
    抽取没给模块时返回空列表（调用方退回到扁平的 _feature_candidates）。
    """
    extracted = source.metadata.get("extracted") if isinstance(source.metadata, dict) else None
    if not isinstance(extracted, dict):
        return []
    modules = extracted.get("modules")
    if not isinstance(modules, list) or not modules:
        return []
    lines: list[dict[str, object]] = []
    for module in modules:
        if not isinstance(module, dict):
            continue
        name = str(module.get("module") or "").strip()
        items = module.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            content = str(item).strip()
            if content:
                lines.append(
                    {"content": content, "module_key": name or None, "module_name": name or None}
                )
    return lines


def _primary_change_type(current_version: int, feature_changes: list[dict[str, Any]]) -> str:
    """从 feature 级变更推导版本主类型，供时间线与审计展示。"""
    if current_version == 0:
        return "new"
    operations = {str(item.get("op") or "") for item in feature_changes}
    if "modify" in operations:
        return "modify"
    if "delete" in operations and "add" not in operations:
        return "delete"
    if "add" in operations:
        return "add"
    return "modify"


def commit_requirement_node(state: dict[str, Any]) -> dict[str, Any]:
    """approved：allocate/get REQ → feature 变更 → version → master → 审计/outbox。

    节点内依赖调用方持有的事务：
    版本、feature、source 状态、审计和 embedding outbox 必须在同一事务里原子提交。
    """
    ctx = _ctx(state)
    session = ctx["session"]
    source_id = ctx["source_id"]
    reviewer_id = ctx["reviewer_id"]
    comment = ctx.get("comment")
    edited_requirement = ctx.get("edited_requirement")
    decision = ctx["decision"]
    review: RequirementReview = ctx["_review"]
    source = _source(state)
    feature_overrides = ctx.get("feature_overrides") or []

    master: RequirementMaster | None = None
    target_key = ctx.get("requirement_key")
    if target_key:
        master = ctx["master_repo"].get_by_key(str(target_key), session=session)

    canonical_req_title = canonical_title(source, edited_requirement)
    canonical_req_text = canonical_requirement(source, edited_requirement)
    # 新建 REQ：抽取给了模块结构就按模块展开（feature 带上模块标签）；
    # 否则退回扁平候选。合并进既有 REQ 时保持扁平（模块的合并管理留到 E 批）。
    if master is None:
        feature_candidates = _module_lines(source) or _feature_candidates(source, edited_requirement)
    else:
        feature_candidates = _feature_candidates(source, edited_requirement)

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
    feature_changes: list[dict[str, Any]] = []
    feature_repo = ctx.get("feature_repo")
    if feature_repo is not None and master.id is not None:
        if current_version == 0:
            # 新建 REQ：把功能条目整体视为本版本新增。
            feature_repo.create_features(
                int(master.id),
                feature_candidates,
                source_id=source.id,
                requirement_key=master.requirement_key,
                version_no=next_version,
                session=session,
            )
            feature_changes = [
                {"op": "add", "feature_key": item["feature_key"], "content": item["content"]}
                for item in feature_repo.list_active(int(master.id), session=session)
            ]
        elif feature_overrides:
            # 合并模式：人工明确给出 add/modify/delete/keep，优先于自动 diff。
            feature_changes = feature_repo.apply_overrides(
                int(master.id),
                feature_overrides,
                source_id=source.id,
                requirement_key=master.requirement_key,
                version_no=next_version,
                session=session,
            )
        else:
            # 无 overrides 时按当前来源与现有 active features 做保守同步。
            feature_changes = feature_repo.sync_features(
                int(master.id),
                feature_candidates,
                source_id=source.id,
                requirement_key=master.requirement_key,
                version_no=next_version,
                session=session,
            )
        joined = feature_repo.join_active_features(int(master.id), session=session)
        if joined:
            # master.final_requirement 始终以当前 active features 的确定性拼接结果为准。
            canonical_req_text = joined

    version = RequirementVersion(
        requirement_id=int(master.id or 0),
        parent_version_id=None,
        parent_version_no=current_version or None,
        version_no=next_version,
        version_title=canonical_req_title[:80],
        change_type=_primary_change_type(current_version, feature_changes),
        requirement_snapshot=canonical_req_text,
        change_summary=comment or "审核通过并生成版本快照",
        feature_changes=feature_changes,
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
            "feature_overrides": feature_overrides,
            "feature_changes": feature_changes,
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

    # —— 需求关系边：把分析阶段算出来的 related/conflict/duplicate 候选落表 ——
    # 放在 master 落库之后：关系的两端都必须已经是存在的 REQ。
    # 标记为 proposed 而非 confirmed ——审核人批准的是**这条需求**，不是分析给出的
    # 每一条关系判断，替人下结论不合适；确认/驳回由 UI 上的裁决入口完成。
    ctx["relation_repo"].upsert_many(
        subject_requirement_id=int(master.id or 0),
        subject_requirement_key=str(master.requirement_key),
        relations=_analysis_relations(
            source.metadata.get("analysis") or ctx.get("analysis_snapshot") or {},
            master_repo=ctx["master_repo"],
            session=session,
            exclude_key=str(master.requirement_key),
        ),
        source_id=source_id,
        session=session,
    )

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
    """rejected/returned：来源置对应状态并记录审计，不创建版本。"""
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
