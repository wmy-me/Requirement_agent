import { describe, expect, it } from "vitest";
import { buildReviewDecisionPayload, canSubmitReview } from "./reviewDecision";

describe("review decision guard", () => {
  it("keeps a snowflake source id byte-for-byte in the JSON payload", () => {
    const sourceId = "9007199254740993123";
    const payload = buildReviewDecisionPayload({ sourceId, decision: "rejected", targetKey: "", mergeMode: "union", reviewer: "", comment: "" });
    expect(JSON.stringify(payload)).toContain(`\"source_id\":\"${sourceId}\"`);
  });

  it("locks every decision after a 409 and prevents duplicate submits", () => {
    for (const decision of ["approved", "rejected", "returned"] as const) {
      expect(canSubmitReview({ isPending: true, conflictLocked: true, isSubmitting: false, decision, hasTarget: true, previewReady: true })).toBe(false);
      expect(canSubmitReview({ isPending: true, conflictLocked: false, isSubmitting: true, decision, hasTarget: true, previewReady: true })).toBe(false);
    }
  });

  it("does not expose decisions for historical sources", () => {
    expect(canSubmitReview({ isPending: false, conflictLocked: false, isSubmitting: false, decision: "rejected", hasTarget: false, previewReady: false })).toBe(false);
  });
});
