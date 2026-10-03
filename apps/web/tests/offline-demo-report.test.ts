import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import { buildOfflineDemoReport, type OfflineDemoReportSource } from "../lib/offline-demo-report.ts";

function fixture(): OfflineDemoReportSource {
  const patch = "--- a/status.py\n+++ b/status.py\n@@ -1 +1 @@\n-return value\n+return value.strip() # café\n";
  return {
    mode: "local_deterministic",
    run: { run_id: "849fe0d9-e6e5-4dac-897d-95e4aeb420e8", state: "AWAITING_APPROVAL" },
    review: {
      patch, patch_hash: createHash("sha256").update(patch).digest("hex"),
      verdict_hash: "b".repeat(64), verdict: "passed", changed_paths: ["status.py"],
      checks: [{ check_id: "public_tests", kind: "test", status: "passed", required: true }]
    },
    decision: null,
    safety: { provider: "offline", model: "forge-deterministic-demo-v1", model_calls: 0, network_requests: 0, cost_microusd: 0, github_writes: 0 }
  };
}

function decided(choice = "approved"): OfflineDemoReportSource {
  const source = fixture();
  source.decision = {
    decision: choice, recorded_at: "2026-10-03T12:30:00.123456+00:00",
    patch_hash: source.review.patch_hash, verdict_hash: source.review.verdict_hash,
    authorizes_github_write: false
  };
  return source;
}

test("pending review exports exact UTF-8 patch, checks and honest limitations", async () => {
  const source = fixture();
  const report = await buildOfflineDemoReport(source);
  assert.equal(report.schema_version, 1);
  assert.equal(report.evidence_kind, "deterministic_offline_demo");
  assert.equal(report.review.patch, source.review.patch);
  assert.equal(report.review.patch_hash, source.review.patch_hash);
  assert.deepEqual(report.review.checks, source.review.checks);
  assert.equal(report.decision, null);
  assert.match(report.disclosure, /not live AI quality, benchmark performance, production readiness/);
  assert.match(report.review.verdict_hash_verification, /not included or independently hashed/);
  assert.match(report.limitations.join(" "), /not a signed attestation/);
});

test("both saved decisions retain evidence binding and never grant publication", async () => {
  for (const choice of ["approved", "rejected"]) {
    const source = decided(choice);
    const report = await buildOfflineDemoReport(source);
    assert.deepEqual(report.decision, source.decision);
    assert.equal(report.decision?.authorizes_github_write, false);
  }
});

test("unknown fields, identities, retry keys and raw events cannot leak through export", async () => {
  const source = decided();
  Object.assign(source, { secret: "private-marker", events: [{ payload: "private-marker", actor: "private-marker" }] });
  for (const object of [source.run, source.review, source.review.checks[0], source.decision!, source.safety]) {
    Object.assign(object, { actor: "private-marker", decision_key: "private-marker", environment: "private-marker" });
  }
  const json = JSON.stringify(await buildOfflineDemoReport(source));
  assert.doesNotMatch(json, /private-marker|decision_key|"events"|"actor"|"environment"/);
});

test("non-offline mode, model, provider, usage or external writes fail closed", async () => {
  for (const update of [
    { provider: "openai" }, { model: "other" }, { model_calls: 1 },
    { network_requests: 1 }, { cost_microusd: 1 }, { github_writes: 1 }, { model_calls: undefined }
  ]) {
    const source = fixture();
    Object.assign(source.safety, update);
    await assert.rejects(buildOfflineDemoReport(source), /incomplete or inconsistent/);
  }
  await assert.rejects(buildOfflineDemoReport({ ...fixture(), mode: "live" }), /incomplete or inconsistent/);
});

test("altered patch bytes, including trailing newline, fail the hash check", async () => {
  for (const transform of [(text: string) => text + "\n", (text: string) => text.trimEnd(), (text: string) => text.replace("strip", "lower")]) {
    const source = fixture();
    source.review.patch = transform(source.review.patch);
    await assert.rejects(buildOfflineDemoReport(source), /incomplete or inconsistent/);
  }
});

test("stale decision hashes, unknown decisions, invalid times and write permission are rejected", async () => {
  for (const update of [
    { patch_hash: "c".repeat(64) }, { verdict_hash: "c".repeat(64) },
    { decision: "pending" }, { recorded_at: "private-marker" }, { authorizes_github_write: true }
  ]) {
    const source = decided();
    Object.assign(source.decision!, update);
    await assert.rejects(buildOfflineDemoReport(source), /incomplete or inconsistent/);
  }
});

test("malformed identifiers, paths, check labels and hashes never serialize arbitrary values", async () => {
  const variants = [
    { ...fixture(), run: { run_id: "../../private", state: "AWAITING_APPROVAL" } },
    { ...fixture(), run: { ...fixture().run, state: "RUNNING" } }
  ];
  for (const path of ["C:/private/status.py", "/private/status.py", "../status.py", "a/../status.py"]) {
    const source = fixture(); source.review.changed_paths = [path]; variants.push(source);
  }
  for (const update of [{ verdict_hash: { secret: "private-marker" } }, { verdict: null }, { checks: [{ check_id: undefined, kind: "test", status: "passed", required: true }] }]) {
    const source = fixture(); Object.assign(source.review, update); variants.push(source);
  }
  for (const source of variants) await assert.rejects(buildOfflineDemoReport(source));
});

test("report owns its arrays and preserves unsuccessful checks without claiming they passed", async () => {
  const source = fixture(); source.review.checks[0].status = "failed";
  const report = await buildOfflineDemoReport(source);
  source.review.changed_paths.push("later.py"); source.review.checks[0].status = "passed";
  assert.deepEqual(report.review.changed_paths, ["status.py"]);
  assert.equal(report.review.checks[0].status, "failed");
});
