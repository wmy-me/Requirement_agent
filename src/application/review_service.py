"""Application service for review decisions."""

from __future__ import annotations

from src.domain.requirement import RequirementReview
from src.infrastructure.db.repositories import RequirementReviewRepository


class ReviewService:
    """Handles reviewer decisions and review persistence."""

    def __init__(self, review_repo: RequirementReviewRepository | None = None) -> None:
        self.review_repo = review_repo or RequirementReviewRepository()

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
    ) -> dict[str, str]:
        review = RequirementReview(
            source_id=source_id,
            analysis_snapshot=analysis_snapshot or {"status": "pending_review"},
            decision=decision,
            reviewer_id=reviewer_id,
            reviewer_name=reviewer_name,
            review_comment=comment,
            edited_requirement=edited_requirement,
        )
        self.review_repo.save(review)
        return {
            "decision": review.decision,
            "reviewer_id": review.reviewer_id,
            "status": "recorded",
        }
