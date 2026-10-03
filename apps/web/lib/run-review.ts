type ReviewableRun = {
  state: string;
  evaluated_patch_hash?: string | null;
  evaluation_verdict_hash?: string | null;
};

type ReviewEvent = { event_type: string; payload: Record<string, unknown> };

export function shouldLoadRunReview(
  run: ReviewableRun | null,
  events: readonly ReviewEvent[]
): boolean {
  if (!run) return false;
  if (run.state === "AWAITING_APPROVAL" || run.state === "PUBLISHING") return true;
  if (run.state !== "COMPLETED") return false;

  // A command-only run can complete without ever producing a reviewable patch.
  // Partial evidence must still reach the API, which validates it fail-closed.
  // SSE state updates can precede refreshed run metadata, so also check events.
  return run.evaluated_patch_hash != null || run.evaluation_verdict_hash != null
    || events.some(event => event.event_type === "evaluation_completed"
      || (event.event_type === "state_changed"
        && ["AWAITING_APPROVAL", "PUBLISHING"].includes(String(event.payload.to_state))));
}
