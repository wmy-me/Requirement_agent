"""审核决策的应用服务（事务外壳，落库逻辑在 LangGraph 决策图节点中）。"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from src.graph.graphs import run_decision
from src.infrastructure.db.repositories import (
    AuditRepository,
    RequirementMasterRepository,
    RequirementReviewRepository,
    RequirementSourceRepository,
    RequirementVersionRepository,
)
from src.infrastructure.db.session import SessionLocal
from src.infrastructure.worker.outbox import OutboxRepository


class ReviewService:
    """负责审核人决策的会话持有者：开事务 → 跑决策图 → commit/rollback/close。"""

    def __init__(
        self,
        review_repo: RequirementReviewRepository | None = None,
        source_repo: RequirementSourceRepository | None = None,
        master_repo: RequirementMasterRepository | None = None,
        version_repo: RequirementVersionRepository | None = None,
        audit_repo: AuditRepository | None = None,
        outbox_repo: OutboxRepository | None = None,
        session_factory: Callable[[], Session] = SessionLocal,
    ) -> None:
        self.review_repo = review_repo or RequirementReviewRepository()
        self.source_repo = source_repo or RequirementSourceRepository()
        self.master_repo = master_repo or RequirementMasterRepository()
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
        reviewer_name: str | None = None,
        comment: str | None = None,
        edited_requirement: str | None = None,
        analysis_snapshot: dict[str, object] | None = None,
        requirement_key: str | None = None,
    ) -> dict[str, object]:
        if decision not in {"approved", "rejected", "returned"}:
            raise ValueError("decision must be approved, rejected, or returned")

        session = self.session_factory()
        ctx = {
            "session": session,
            "review_repo": self.review_repo,
            "source_repo": self.source_repo,
            "master_repo": self.master_repo,
            "version_repo": self.version_repo,
            "audit_repo": self.audit_repo,
            "outbox_repo": self.outbox_repo,
            "source_id": source_id,
            "decision": decision,
            "reviewer_id": reviewer_id,
            "reviewer_name": reviewer_name,
            "comment": comment,
            "edited_requirement": edited_requirement,
            "analysis_snapshot": analysis_snapshot,
            "requirement_key": requirement_key,
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
