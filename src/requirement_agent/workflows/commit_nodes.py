"""决策子图：人工审核 → 落库节点（commit/reject）。

节点从 ctx（运行时注入的 repos + session + 审核入参）读写数据库；
事务边界（commit/rollback/close）由调用方 ReviewService 持有，节点内不 commit。
"""

from __future__ import annotations

from typing import Any, Literal

from requirement_agent.agents.analyze_agent import thresholds_for
from requirement_agent.domain.requirement_titles import derive_title_candidates
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


def _merge_confirmations(
    analysis: dict[str, Any],
    *,
    master_repo: Any,
    session: Any,
    merge_key: str,
) -> list[dict[str, object]]:
    """合并发生时，把「目标 REQ 与其它的重复候选」的 `duplicates_of` 边确认下来。

    **只有「合并目标本身就是分析认定的那个重复对象」时才确认任何东西。** 人工合并
    不一定是因为重复（也可能只是想并入），那种情况下替人确认重复关系是不对的。

    为什么不去确认「来源 ⤳ 目标 REQ」那条边：`requirement_relation` 的两端都是
    `NOT NULL REFERENCES requirement_master(id)`（`migrations/009`），而来源此时还不是
    正式 REQ，**表结构根本装不下这条边**。所以这里的语义是「目标 REQ 吸收来源后，
    它与其它重复候选的关系获得了人的背书」。
    """
    if not analysis.get("duplicate"):
        return []
    threshold = thresholds_for(None)["duplicate"]

    def duplicates() -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for candidate in analysis.get("candidates") or []:
            if not isinstance(candidate, dict):
                continue
            try:
                similarity = float(candidate.get("similarity") or 0.0)
            except (TypeError, ValueError):
                similarity = 0.0
            if similarity >= threshold:
                found.append({**candidate, "similarity": similarity})
        return found

    duplicate_candidates = duplicates()
    if not any(
        str(item.get("requirement_key") or "").strip() == merge_key
        for item in duplicate_candidates
    ):
        return []  # 合并目标不是重复候选 → 不替人确认任何关系

    confirmations: list[dict[str, object]] = []
    for candidate in duplicate_candidates:
        target_key = str(candidate.get("requirement_key") or "").strip()
        if not target_key or target_key == merge_key:
            continue
        target = master_repo.get_by_key(target_key, session=session)
        if target is None or target.id is None:
            continue
        confirmations.append(
            {
                "target_requirement_id": target.id,
                "target_requirement_key": target_key,
                "relation_type": "duplicates_of",
                "reason": candidate.get("reason"),
                "similarity": candidate["similarity"],
            }
        )
    return confirmations


def _normalize_text(value: Any) -> str:
    """归一化文本用于对应：去掉所有空白（含全角空格）。"""
    return "".join(str(value or "").split())


def _build_capability_links(
    active_features: list[dict[str, Any]],
    match_payload: dict[str, Any],
) -> list[dict[str, object]]:
    """把分析阶段匹配到的能力挂到对应的 feature 上（批次 3）。

    靠 `capabilities[].raw_text` 与 `feature.content` 的**归一后精确匹配**建立对应 ——
    抽取时 `raw_text` 本该就是那条子需求的原话。

    **对不上就不挂**：宁可少挂，也不要挂错。挂错会让能力视图里出现
    「这条需求有这个能力」的假象，而它正是人工审核要依赖的东西。
    """
    by_text: dict[str, dict[str, Any]] = {}
    for hit in (match_payload or {}).get("capabilities") or []:
        if not isinstance(hit, dict) or not hit.get("capability_id"):
            continue
        key = _normalize_text(hit.get("raw_text"))
        if key:
            by_text.setdefault(key, hit)

    links: list[dict[str, object]] = []
    for feature in active_features:
        hit = by_text.get(_normalize_text(feature.get("content")))
        if hit is None:
            continue
        links.append(
            {
                "feature_id": int(feature["id"]),
                "capability_id": int(hit["capability_id"]),
                # 用 feature 的正文而不是模型改写的 raw_text：审核页要展示的是
                # 落库后的功能原文，两者并排比对才有意义
                "raw_text": str(feature.get("content") or "")[:2000],
            }
        )
    return links


def _capability_snapshot(
    active_features: list[dict[str, Any]],
    match_payload: dict[str, Any],
    links: list[dict[str, object]],
) -> list[dict[str, object]]:
    """按**本版本的功能集**冻结一份能力快照。

    一个能力可能由多条功能支撑（如「导出 Excel」既有「导出报表」也有「批量导出」），
    所以按 capability 聚合出 `feature_keys`。
    """
    hits = {
        int(hit["capability_id"]): hit
        for hit in (match_payload or {}).get("capabilities") or []
        if isinstance(hit, dict) and hit.get("capability_id")
    }
    by_feature = {int(link["feature_id"]): link for link in links}

    grouped: dict[int, dict[str, object]] = {}
    for feature in active_features:
        link = by_feature.get(int(feature["id"]))
        if link is None:
            continue
        capability_id = int(link["capability_id"])
        entry = grouped.setdefault(
            capability_id,
            {
                "capability_id": capability_id,
                "action": hits.get(capability_id, {}).get("action"),
                "object": hits.get(capability_id, {}).get("object"),
                "display_name": hits.get(capability_id, {}).get("display_name"),
                "review_status": "proposed",
                "feature_keys": [],
            },
        )
        entry["feature_keys"].append(feature.get("feature_key"))
    return list(grouped.values())


def _constraint_snapshot(match_payload: dict[str, Any]) -> list[dict[str, object]]:
    """冻结条件匹配结果：命中的记正式键，未命中的只记原文。

    未命中的**记下来但不入词表** —— 人工审核时要能看到「模型提过这个条件」，
    才有依据决定是合并、新增、作为别名，还是不结构化。
    """
    constraints = (match_payload or {}).get("constraints") or {}
    snapshot: list[dict[str, object]] = []
    for item in constraints.get("matched") or []:
        if isinstance(item, dict):
            snapshot.append(
                {
                    "raw": item.get("raw"),
                    "constraint_key": item.get("constraint_key"),
                    "matched": True,
                    "alias_hit": bool(item.get("alias_hit")),
                }
            )
    for item in constraints.get("unmatched") or []:
        if isinstance(item, dict):
            snapshot.append(
                {"raw": item.get("raw"), "constraint_key": None, "matched": False}
            )
    return snapshot


def feature_rows_for_source(source: Any, edited_requirement: str | None) -> list[Any]:
    """来源 → 参与版本治理的功能行。**唯一入口，新建支与合并支都必须用它。**

    抽取给了模块结构就按模块展开（行带 `module_key` / `module_name`），否则退回扁平候选。

    合并路径曾经被强制降级成扁平候选（注释写「模块的合并管理留到 E 批」），后果是
    「合进来的功能全都没有模块标签」。E 批的预合并预览又必须按带模块的行来分组 ——
    两处不用同一个入口，预览与落库就会对不上。
    """
    return _module_lines(source) or _feature_candidates(source, edited_requirement)


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
    # 来源功能行：新建与合并**共用同一个入口**，否则合并路径会丢掉模块标签，
    # 而预览是带模块算的 —— 两边对不上。
    feature_candidates = feature_rows_for_source(source, edited_requirement)

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
            # 无 overrides 时按当前来源与现有 active features 同步。
            # `prune` 只由人工在「合并并选择以来源为准」时开启：默认是并集，
            # 来源没提到的现有功能一律保留。回滚（G 批）会显式传 True。
            feature_changes = feature_repo.sync_features(
                int(master.id),
                feature_candidates,
                source_id=source.id,
                requirement_key=master.requirement_key,
                version_no=next_version,
                prune=str(ctx.get("merge_mode") or "union") == "replace",
                session=session,
            )
        joined = feature_repo.join_active_features(int(master.id), session=session)
        if joined:
            # master.final_requirement 始终以当前 active features 的确定性拼接结果为准。
            canonical_req_text = joined

    # —— 能力关联与版本快照（批次 3）——
    # 必须在 version 构造**之前**算好：快照要随版本一起落库。
    # 关联一律写 proposed（仓储层在 SQL 里写死），**只有人工确认才代表能力正式成立**。
    capability_links: list[dict[str, object]] = []
    capability_snapshot: list[dict[str, object]] = []
    constraint_snapshot: list[dict[str, object]] = []
    feature_capability_repo = ctx.get("feature_capability_repo")
    if feature_repo is not None and master.id is not None:
        active_features = feature_repo.list_active(int(master.id), session=session)
        match_payload = source.metadata.get("capability_match") or {}
        capability_links = _build_capability_links(active_features, match_payload)
        capability_snapshot = _capability_snapshot(active_features, match_payload, capability_links)
        constraint_snapshot = _constraint_snapshot(match_payload)
        if feature_capability_repo is not None and capability_links:
            feature_capability_repo.link_many(links=capability_links, session=session)

    # —— 候选标题（同一需求的不同视角入口）——
    # 从**已有数据**派生，不调模型：同一个需求的几种叫法本来就对应业务对象/能力/条件。
    # 一律 proposed；列表只出 confirmed，所以这里不会让人看到没确认过的叫法。
    title_repo = ctx.get("title_repo")
    if title_repo is not None and master.id is not None:
        extracted_meta = source.metadata.get("extracted") or {}
        capability_hits = (source.metadata.get("capability_match") or {}).get("capabilities") or []
        constraint_keys = [
            item["constraint_key"]
            for item in (source.metadata.get("capability_match") or {})
            .get("constraints", {})
            .get("matched", [])
            if item.get("constraint_key")
        ]
        derived = derive_title_candidates(
            business_object=str(extracted_meta.get("business_object") or ""),
            capabilities=[
                {
                    "action": hit.get("action"),
                    "object": hit.get("object"),
                    "capability_id": hit.get("capability_id"),
                }
                for hit in capability_hits
                if isinstance(hit, dict)
            ],
            constraint_keys=constraint_keys,
        )
        if derived:
            title_repo.upsert_many(requirement_id=int(master.id), titles=derived, session=session)

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
        capability_snapshot=capability_snapshot,
        constraint_snapshot=constraint_snapshot,
        diff_payload={
            "decision": decision,
            "reviewer_id": reviewer_id,
            "reviewer_name": ctx.get("reviewer_name"),
            "edited_requirement": edited_requirement,
            # 本条来源并入了哪个既有 REQ（None = 新建）。F 批画版本链要读它。
            "target_requirement_key": target_key,
            "merge_mode": str(ctx.get("merge_mode") or "union"),
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
    # ⚠️ **必须先把旧的 current 降级，再插入新版本。**
    # 数据库的部分唯一索引不允许同主线两个 current 并存，而 save() 插入时状态默认
    # 就是 current —— 顺序反过来会在**第二条版本**上直接撞唯一约束（实测踩过一次）。
    ctx["version_repo"].supersede_current(
        requirement_id=int(master.id or 0), keep_version_no=next_version, session=session
    )
    saved_version = ctx["version_repo"].save(version, session=session)
    if saved_version.id is not None and source.id is not None:
        ctx["version_repo"].link_source(saved_version.id, source.id, session=session)

    # 合并进既有 REQ 时**保留原标题**：把一条小来源并进大 REQ，不该把大 REQ 改名成
    # 小来源的名字。只有人工显式改写（edited_requirement）时才跟随新标题。
    if target_key is None or (edited_requirement or "").strip():
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

    # —— 合并即确认：人工把来源并进某个 REQ，本身就为「它与该 REQ 重复」背了书 ——
    # 但 `requirement_relation` 装不下「来源 ⤳ 目标 REQ」（两端都是已存在的 REQ），
    # 所以这里确认的是「目标 REQ 与它**其它**重复候选」的边。目标 REQ 不是重复候选时
    # 一条都不确认 —— 人工合并不一定是因为重复。
    if target_key:
        confirmations = _merge_confirmations(
            source.metadata.get("analysis") or ctx.get("analysis_snapshot") or {},
            master_repo=ctx["master_repo"],
            session=session,
            merge_key=str(master.requirement_key),
        )
        if confirmations:
            ctx["relation_repo"].confirm_many(
                subject_requirement_id=int(master.id or 0),
                subject_requirement_key=str(master.requirement_key),
                relations=confirmations,
                source_id=source_id,
                decided_by=reviewer_id,
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
