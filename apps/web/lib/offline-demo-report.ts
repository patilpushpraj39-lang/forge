/** Only the public offline fixture's review fields are eligible for export. */
export type OfflineDemoReportSource = {
  mode: string;
  run: { run_id: string; state: string };
  review: {
    patch: string;
    patch_hash: string;
    verdict_hash: string;
    verdict: string;
    changed_paths: string[];
    checks: { check_id: string; kind: string; status: string; required: boolean }[];
  };
  decision: {
    decision: string;
    recorded_at: string;
    patch_hash: string;
    verdict_hash: string;
    authorizes_github_write: boolean;
  } | null;
  safety: {
    provider: string;
    model: string;
    model_calls: number;
    network_requests: number;
    cost_microusd: number;
    github_writes: number;
  };
};

const invalidEvidence = "The offline evidence is incomplete or inconsistent. Reload the demo before downloading.";
const hashPattern = /^[a-f0-9]{64}$/;
const labelPattern = /^[a-zA-Z0-9_-]{1,100}$/;
const isLabel = (value: unknown): value is string => typeof value === "string" && labelPattern.test(value);
const isHash = (value: unknown): value is string => typeof value === "string" && hashPattern.test(value);

export async function buildOfflineDemoReport(source: OfflineDemoReportSource) {
  const { run, review, safety, decision } = source;
  if (
    source.mode !== "local_deterministic" ||
    safety?.provider !== "offline" || safety.model !== "forge-deterministic-demo-v1" ||
    [safety.model_calls, safety.network_requests, safety.cost_microusd, safety.github_writes].some((value) => value !== 0) ||
    typeof run?.run_id !== "string" || !/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(run.run_id) ||
    run.state !== "AWAITING_APPROVAL" ||
    typeof review?.patch !== "string" || review.patch.length === 0 || review.patch.length > 1_000_000 ||
    !isHash(review.patch_hash) || !isHash(review.verdict_hash) ||
    !isLabel(review.verdict) ||
    !Array.isArray(review.changed_paths) || review.changed_paths.length === 0 ||
    review.changed_paths.some((path) => typeof path !== "string" || !/^[\w.-]+(?:\/[\w.-]+)*$/.test(path) || path.split("/").some((part) => part === "." || part === "..")) ||
    !Array.isArray(review.checks) || review.checks.length === 0 ||
    review.checks.some((check) => !check || !isLabel(check.check_id) || !isLabel(check.kind) || !isLabel(check.status) || typeof check.required !== "boolean")
  ) throw new Error(invalidEvidence);

  if (decision !== null && (
    !decision || !["approved", "rejected"].includes(decision.decision) ||
    typeof decision.recorded_at !== "string" ||
    !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)$/.test(decision.recorded_at) ||
    !Number.isFinite(Date.parse(decision.recorded_at)) ||
    decision.patch_hash !== review.patch_hash || decision.verdict_hash !== review.verdict_hash ||
    decision.authorizes_github_write !== false
  )) throw new Error(invalidEvidence);

  // Verify the exact UTF-8 patch, not the whitespace-trimmed on-screen preview.
  const digest = await globalThis.crypto.subtle.digest("SHA-256", new TextEncoder().encode(review.patch));
  const patchHash = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
  if (patchHash !== review.patch_hash) throw new Error(invalidEvidence);

  // Whitelist fields: never serialize the response, raw events, actors or retry keys.
  return {
    schema_version: 1,
    evidence_kind: "deterministic_offline_demo",
    disclosure: "A scripted run against a retired public smoke fixture. This is engineering-workflow evidence, not live AI quality, benchmark performance, production readiness or permission to publish.",
    task: "Normalize user-entered status strings",
    run: { run_id: run.run_id, state: run.state },
    review: {
      patch: review.patch,
      patch_hash: patchHash,
      patch_hash_verification: "SHA-256 recomputed in the browser over the exact UTF-8 patch",
      verdict_hash: review.verdict_hash,
      verdict_hash_verification: "Backend artifact reference only; the complete verdict artifact is not included or independently hashed by this export",
      verdict: review.verdict,
      changed_paths: [...review.changed_paths],
      checks: review.checks.map((check) => ({
        check_id: check.check_id, kind: check.kind, status: check.status, required: check.required
      }))
    },
    decision: decision === null ? null : {
      decision: decision.decision,
      recorded_at: decision.recorded_at,
      patch_hash: decision.patch_hash,
      verdict_hash: decision.verdict_hash,
      authorizes_github_write: false
    },
    safety: {
      provider: "offline", model: safety.model, model_calls: 0,
      external_runtime_requests: 0, cost_microusd: 0, github_writes: 0
    },
    limitations: [
      "The local backend supplies checks, verdict and decision; this file is editable, not a signed attestation.",
      "A null decision means no saved approve/reject decision was loaded when downloaded.",
      "External runtime requests exclude browser requests to the local Forge API and authentication service.",
      "No raw event payloads, reviewer identities, retry keys or environment configuration are exported."
    ]
  };
}
