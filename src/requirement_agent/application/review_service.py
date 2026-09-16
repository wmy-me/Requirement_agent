"""审核决策的应用服务（事务外壳，落库逻辑在 LangGraph 决策图节点中）。"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from requirement_agent.workflows.commit_nodes import feature_rows_for_source
from requirement_agent.workflows.graphs import run_decision
from requirement_agent.infrastructure.db.repositories import (
    AuditRepository,
    RequirementFeatureRepository,
    RequirementMasterRepository,
    RequirementRelationRepository,
    RequirementReviewRepository,
    RequirementSourceRepository,
    RequirementVersionRepository,
)
from requirement_agent.infrastructure.db.session import SessionLocal
from requirement_agent.infrastructure.worker.outbox import OutboxRepository


class ReviewService:
    """负责审核人决策的会话持有者：开事务 → 跑决策图 → commit/rollback/close。"""

    def __init__(
        self,
        review_repo: RequirementReviewRepository | None = None,
        source_repo: RequirementSourceRepository | None = None,
        master_repo: RequirementMasterRepository | None = None,
        feature_repo: RequirementFeatureRepository | None = None,
        version_repo: RequirementVersionRepository | None = None,
        audit_repo: AuditRepository | None = None,
        outbox_repo: OutboxRepository | None = None,
        relation_repo: RequirementRelationRepository | None = None,
        session_factory: Callable[[], Session] = SessionLocal,
    ) -> None:
        self.review_repo = review_repo or RequirementReviewRepository()
        self.source_repo = source_repo or RequirementSourceRepository()
        self.master_repo = master_repo or RequirementMasterRepository()
        self.feature_repo = feature_repo or RequirementFeatureRepository()
        self.version_repo = version_repo or RequirementVersionRepository()
        self.audit_repo = audit_repo or AuditRepository()
        self.outbox_repo = outbox_repo or OutboxRepository()
        self.relation_repo = relation_repo or RequirementRelationRepository()
        self.session_factory = session_factory

    def submit_decision(
        self,
        *,
        source_id: int,
        decision: str,
        reviewer_id: str,
        target_requirement_key: str | None = None,
        reviewer_name: str | None = None,
        comment: str | None = None,
        edited_requirement: str | None = None,
        feature_overrides: list[dict[str, object]] | None = None,
        analysis_snapshot: dict[str, object] | None = None,
        requirement_key: str | None = None,
        merge_mode: str = "union",
    ) -> dict[str, object]:
        """人工审核入口（会话持有者，事务边界）。

        - decision 取值 approved / rejected / returned，其余抛 ValueError。
        - 事务语义：本服务只负责创建 session 并在决策图跑完后 commit；
          异常一律 rollback 后重抛，finally 关闭 session。
        - 真正的落库（requirement_review / master / feature / version /
          source 状态 / audit / outbox）在 LangGraph 决策图节点内按同一 session
          执行，保证原子性；此处不直接写库。
        - target_requirement_key 为“合并进既有 REQ”的入口：非空时审核通过会
          对目标主需求做 feature 级合并；feature_overrides 供人工逐条裁决
          add/modify/delete/keep。
        - merge_mode 取 union（默认，并集：来源没提到的现有功能保留）/ replace
          （以来源为准，未命中的现有功能软删）。仅在合并时生效。
        - 返回 dict(state["outcome"])：含 decision / reviewer_id / status /
          version_no / requirement_key。
        """
        if decision not in {"approved", "rejected", "returned"}:
            raise ValueError("decision must be approved, rejected, or returned")

        session = self.session_factory()
        ctx = {
            "session": session,
            "review_repo": self.review_repo,
            "source_repo": self.source_repo,
            "master_repo": self.master_repo,
            "feature_repo": self.feature_repo,
            "version_repo": self.version_repo,
            "audit_repo": self.audit_repo,
            "outbox_repo": self.outbox_repo,
            "relation_repo": self.relation_repo,
            "source_id": source_id,
            "decision": decision,
            "reviewer_id": reviewer_id,
            "reviewer_name": reviewer_name,
            "comment": comment,
            "edited_requirement": edited_requirement,
            "feature_overrides": feature_overrides or [],
            "analysis_snapshot": analysis_snapshot,
            "requirement_key": target_requirement_key or requirement_key,
            "merge_mode": merge_mode or "union",
        }
        try:
            state = run_decision(ctx)
            session.commit()
            return dict(state["outcome"])
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def preview_merge(
        self,
        *,
        source_id: int,
        target_requirement_key: str,
        merge_mode: str = "union",
    ) -> dict[str, object]:
        """只读预演：把这条来源并进目标 REQ，会造成哪些功能级增删改。

        与 `commit_requirement_node` 真正落库的那条路**共用同一个内核**
        （`feature_rows_for_source` + `RequirementFeatureRepository.preview_sync`），
        所以这里列出的 add/modify/delete 就是提交后会真实发生的。**全程不开写事务。**

        异常约定：来源不存在 / 目标 REQ 不存在 → `LookupError`；
        来源不在 `pending_review` → `ValueError`。
        """
        source = self.source_repo.get_by_id(source_id)
        if source is None:
            raise LookupError(f"source_id={source_id} not found")
        if source.processing_status != "pending_review":
            raise ValueError(f"source_id={source_id} is not pending review")

        master = self.master_repo.get_by_key(target_requirement_key)
        if master is None or master.id is None:
            raise LookupError(f"requirement_key={target_requirement_key} not found")

        mode = "replace" if str(merge_mode or "union") == "replace" else "union"
        plan = self.feature_repo.preview_sync(
            int(master.id),
            feature_rows_for_source(source, None),
            prune=mode == "replace",
        )

        groups: dict[tuple[str | None, str | None], dict[str, object]] = {}

        def _bucket(module_key: str | None, module_name: str | None) -> dict[str, object]:
            key = (module_key, module_name)
            if key not in groups:
                groups[key] = {
                    "module_key": module_key,
                    "module_name": module_name,
                    "added": [],
                    "modified": [],
                    "deleted": [],
                    "kept": 0,
                }
            return groups[key]

        counts = {"add": 0, "modify": 0, "delete": 0, "keep": 0}
        overrides: list[dict[str, object]] = []

        for item in plan:
            counts[item.op] += 1
            bucket = _bucket(item.module_key, item.module_name)
            if item.op == "add":
                bucket["added"].append({"content": item.content})
                overrides.append(
                    {
                        "op": "add",
                        "content": item.content,
                        "module_key": item.module_key,
                        "module_name": item.module_name,
                    }
                )
            elif item.op == "modify":
                modified: dict[str, object] = {
                    "feature_key": item.feature_key,
                    "before": item.before,
                    "after": item.content,
                }
                if item.module_before is not None:
                    modified["module_before"] = item.module_before[0]
                    modified["module_after"] = item.module_key
                bucket["modified"].append(modified)
                overrides.append(
                    {
                        "op": "modify",
                        "feature_key": item.feature_key,
                        "content": item.content,
                    }
                )
            elif item.op == "delete":
                bucket["deleted"].append(
                    {"feature_key": item.feature_key, "content": item.content}
                )
                overrides.append({"op": "delete", "feature_key": item.feature_key})
            else:
                bucket["kept"] = int(bucket["kept"]) + 1

        warnings: list[str] = []
        if counts["delete"]:
            warnings.append(
                f"replace 模式：合并后目标需求将失去 {counts['delete']} 条现有功能"
            )
        if not counts["add"] and not counts["modify"] and not counts["delete"]:
            warnings.append("来源的功能行与目标需求完全一致，合并不会产生任何变更")

        active_after = counts["keep"] + counts["modify"] + counts["add"]
        return {
            "source_id": source_id,
            "merge_mode": mode,
            "target": {
                "requirement_key": master.requirement_key,
                "requirement_name": master.requirement_name,
                "current_version": int(master.current_version or 0),
            },
            "next_version": int(master.current_version or 0) + 1,
            "groups": list(groups.values()),
            "summary": {**counts, "active_after": active_after},
            "overrides": overrides,
            "warnings": warnings,
        }
