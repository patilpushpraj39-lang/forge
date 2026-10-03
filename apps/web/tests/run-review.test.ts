import assert from "node:assert/strict";
import { test } from "node:test";
import { shouldLoadRunReview } from "../lib/run-review.ts";

test("completed command-only runs do not request nonexistent review evidence", () => {
  const run = { state: "COMPLETED", evaluated_patch_hash: null, evaluation_verdict_hash: null };
  const events = [
    { event_type: "command_completed", payload: { exit_code: 0 } },
    { event_type: "state_changed", payload: { to_state: "COMPLETED" } },
    { event_type: "workspace_destroyed", payload: {} }
  ];
  assert.equal(shouldLoadRunReview(run, events), false);
  assert.equal(shouldLoadRunReview({ state: "COMPLETED" }, []), false);
});

test("restored completed patch runs still load their review", () => {
  assert.equal(shouldLoadRunReview({ state: "COMPLETED", evaluated_patch_hash: "a".repeat(64),
    evaluation_verdict_hash: "b".repeat(64) }, []), true);
});

test("incomplete patch evidence remains visible to API integrity validation", () => {
  for (const evidence of [{ evaluated_patch_hash: "partial" }, { evaluation_verdict_hash: "partial" },
    { evaluated_patch_hash: "" }]) {
    assert.equal(shouldLoadRunReview({ state: "COMPLETED", ...evidence }, []), true);
  }
});

test("SSE evaluation and approval history work before run metadata refresh", () => {
  const run = { state: "COMPLETED", evaluated_patch_hash: null, evaluation_verdict_hash: null };
  for (const event of [
    { event_type: "evaluation_completed", payload: {} },
    { event_type: "state_changed", payload: { to_state: "AWAITING_APPROVAL" } },
    { event_type: "state_changed", payload: { to_state: "PUBLISHING" } }
  ]) {
    assert.equal(shouldLoadRunReview(run, [event]), true);
  }
});

test("approval and publishing always request review even with absent metadata", () => {
  for (const state of ["AWAITING_APPROVAL", "PUBLISHING"]) {
    assert.equal(shouldLoadRunReview({ state }, []), true);
  }
});

test("non-review states and unrelated events do not request patch review", () => {
  assert.equal(shouldLoadRunReview(null, []), false);
  for (const state of ["CREATED", "SNAPSHOTTING", "EXECUTING", "EVALUATING", "CANCELLED", "FAILED"]) {
    assert.equal(shouldLoadRunReview({ state, evaluated_patch_hash: "a".repeat(64) }, []), false);
  }
  assert.equal(shouldLoadRunReview({ state: "COMPLETED" }, [
    { event_type: "command_completed", payload: { text: "AWAITING_APPROVAL" } }
  ]), false);
});
