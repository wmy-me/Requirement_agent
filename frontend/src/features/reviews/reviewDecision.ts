import type { MergePreview, ReviewDecision } from "../../contracts/api";

/** 审核页的可测试业务规则；ID 绝不能经 Number/parseInt。 */
export function buildReviewDecisionPayload(args: {
  sourceId: string;
  decision: ReviewDecision;
  targetKey: string;
  mergeMode: "union" | "replace";
  reviewer: string;
  comment: string;
  preview?: MergePreview;
}) {
  return {
    source_id: args.sourceId,
    decision: args.decision,
    target_requirement_key: args.targetKey || null,
    merge_mode: args.mergeMode,
    reviewer_name: args.reviewer || null,
    comment: args.comment || null,
    feature_overrides: args.decision === "approved" && args.preview ? args.preview.overrides : [],
  };
}

/** 409 锁、历史只读和重复提交共用同一扇门，不能只保护「批准」。 */
export function canSubmitReview(args: {
  isPending: boolean;
  conflictLocked: boolean;
  isSubmitting: boolean;
  decision: ReviewDecision;
  hasTarget: boolean;
  previewReady: boolean;
}): boolean {
  if (!args.isPending || args.conflictLocked || args.isSubmitting) return false;
  return args.decision !== "approved" || !args.hasTarget || args.previewReady;
}
