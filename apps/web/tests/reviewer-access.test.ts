import assert from "node:assert/strict";
import { test } from "node:test";
import { ApiAccessError } from "../lib/api-client.ts";
import { checkReviewerAccess, reviewerAccessError, reviewerAccessStatus } from "../lib/reviewer-access.ts";

const identity = { actor_id: "clerk:user_fixture", subject: "user_fixture", provider: "clerk" };
const active = { status: "signed-in" } as const;

test("reviewer status distinguishes session loading, sign-out, checking and success", () => {
  assert.equal(reviewerAccessStatus({ status: "unconfigured" }, null, null).message, "Clerk is not configured");
  assert.equal(reviewerAccessStatus({ status: "loading" }, null, null).message, "Loading reviewer session…");
  assert.match(reviewerAccessStatus({ status: "signed-out" }, identity, "stale error").message, /^Sign in/);
  assert.equal(reviewerAccessStatus(active, null, null).message, "Checking reviewer authorization…");
  assert.deepEqual(reviewerAccessStatus(active, identity, null), { message: identity.actor_id, failed: false });
});

test("unavailable backend errors replace checking even before any run or review", () => {
  const message = reviewerAccessError(new ApiAccessError(503));
  assert.deepEqual(reviewerAccessStatus(active, null, message), { message, failed: true });
  assert.equal(reviewerAccessStatus(active, identity, message).failed, true);
  assert.doesNotMatch(message, /Checking/);
});

test("expired and forbidden sessions never display stale reviewer identity", () => {
  for (const accessDenied of [401, 403] as const) {
    const status = reviewerAccessStatus({ ...active, accessDenied }, identity, null);
    assert.equal(status.failed, true);
    assert.notEqual(status.message, identity.actor_id);
    assert.doesNotMatch(status.message, /Checking/);
  }
});

test("unknown reviewer failures do not echo token or provider details", () => {
  const message = reviewerAccessError(new Error("fixture-secret-token / private provider error"));
  assert.match(message, /backend is running/);
  assert.doesNotMatch(message, /fixture-secret|private provider/);
});

test("reviewer check requests the authenticated endpoint and validates its response", async () => {
  const result = await checkReviewerAccess(async (path, init, requireSession) => {
    assert.equal(path, "/auth/me");
    assert.equal(requireSession, true);
    assert.ok(init.signal);
    return Response.json(identity);
  }, new AbortController().signal);
  assert.deepEqual(result, identity);
  await assert.rejects(checkReviewerAccess(async () => Response.json({ actor_id: identity.actor_id }),
    new AbortController().signal), /Invalid reviewer identity/);
  await assert.rejects(checkReviewerAccess(async () => new Response("fixture-secret", { status: 503 }),
    new AbortController().signal), ApiAccessError);
});

test("reviewer deadline settles and aborts a stalled request, ignoring late success", async () => {
  let requestSignal: AbortSignal | undefined;
  let finish!: (response: Response) => void;
  const check = checkReviewerAccess(async (_path, init) => {
    requestSignal = init.signal as AbortSignal;
    return new Promise<Response>(resolve => { finish = resolve; });
  }, new AbortController().signal, 10);
  await assert.rejects(check, /Reviewer check timed out/);
  assert.equal(requestSignal?.aborted, true);
  finish(Response.json(identity));
  await assert.rejects(check, /Reviewer check timed out/);
});

test("leaving a reviewer check cancels it even if token retrieval ignores abort", async () => {
  const controller = new AbortController();
  let called = false;
  const check = checkReviewerAccess(async () => { called = true; return new Promise<Response>(() => {}); },
    controller.signal);
  controller.abort();
  await assert.rejects(check, { name: "AbortError" });
  assert.equal(called, true);
  await assert.rejects(checkReviewerAccess(async () => assert.fail("cancelled check sent"), controller.signal),
    { name: "AbortError" });
});
