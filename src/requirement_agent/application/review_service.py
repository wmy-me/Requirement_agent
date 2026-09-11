"""审核决策的应用服务（事务外壳，落库逻辑在 LangGraph 决策图节点中）。"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from src.requirement_agent.workflows.graphs import run_decision
from src.requirement_agent.infrastructure.db.repositories import (
    AuditRepository,
    RequirementFeatureRepository,
    RequirementMasterRepository,
    RequirementReviewRepository,
    RequirementSourceRepository,
    RequirementVersionRepository,
)
from src.requirement_agent.infrastructure.db.session import SessionLocal
from src.requirement_agent.infrastructure.worker.outbox import OutboxRepository


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
        session_factory: Callable[[], Session] = SessionLocal,
    ) -> None:
        self.review_repo = review_repo or RequirementReviewRepository()
        self.source_repo = source_repo or RequirementSourceRepository()
        self.master_repo = master_repo or RequirementMasterRepository()
        self.feature_repo = feature_repo or RequirementFeatureRepository()
        self.version_repo = version_repo or RequirementVersionRepository()
        self.audit_repo = audit_repo or AuditRepository()
        self.outbox_repo = outbox_repo or OutboxRepository()
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
            "source_id": source_id,
            "decision": decision,
            "reviewer_id": reviewer_id,
            "reviewer_name": reviewer_name,
            "comment": comment,
            "edited_requirement": edited_requirement,
            "feature_overrides": feature_overrides or [],
            "analysis_snapshot": analysis_snapshot,
            "requirement_key": target_requirement_key or requirement_key,
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
