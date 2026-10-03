export const reviewScope = "readonly-preview-record-only-v1";
const effects = ["execution_enabled", "authorizes_source_upload", "authorizes_paid_generation",
  "authorizes_file_changes", "authorizes_github_writes"] as const;
const hash = /^[0-9a-f]{64}$/;
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

export type ReviewReceipt = {
  scope: string; decision: "approved" | "rejected"; actor: string; created_at: string;
  event_id: string; plan_sha256: string;
};
export type ReadonlyPreview = {
  scope: string; readme: string; decision: ReviewReceipt | null;
  plan: {
    kind: string; source_run_id: string; repository: string; base_sha: string;
    plan_sha256: string; snapshot_sha256: string; readme_sha256: string; readme_bytes: number;
    request: { model: string; instructions: string; input: { role: string; content: string }[];
      tools: unknown[]; max_output_tokens: number; reasoning: { effort: string }; service_tier: string; store: boolean };
    limits: { price_date: string; max_exact_input_tokens: number; max_estimate_microusd: number;
      allowance_microusd: number; generation_attempts: number; automatic_retries: number;
      input_microusd_per_million: number; output_microusd_per_million: number };
    unverified: string[];
  };
};

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid preview response");
  return value as Record<string, unknown>;
}
function assertRecordOnly(value: Record<string, unknown>) {
  if (value.scope !== reviewScope || effects.some(key => value[key] !== false)) throw new Error("Invalid review scope");
}
export function previewPath(runId: string, repository: string) {
  if (!uuid.test(runId) || !/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository) || repository.length > 201) {
    throw new Error("Enter a source run ID and owner/repository");
  }
  return `/readonly-previews/${runId}?repository=${encodeURIComponent(repository)}`;
}
export function parseReceipt(value: unknown, fingerprint: string): ReviewReceipt {
  const receipt = object(value);
  assertRecordOnly(receipt);
  if (!hash.test(fingerprint) || receipt.plan_sha256 !== fingerprint
    || !["approved", "rejected"].includes(String(receipt.decision))
    || typeof receipt.actor !== "string" || !receipt.actor.startsWith("clerk:")
    || typeof receipt.created_at !== "string" || !Number.isFinite(Date.parse(receipt.created_at))
    || typeof receipt.event_id !== "string" || !uuid.test(receipt.event_id)) throw new Error("Invalid review receipt");
  return receipt as unknown as ReviewReceipt;
}
export function parsePreview(value: unknown, runId: string, repository: string): ReadonlyPreview {
  const preview = object(value);
  assertRecordOnly(preview);
  const plan = object(preview.plan);
  const request = object(plan.request);
  const limits = object(plan.limits);
  const input = Array.isArray(request.input) && request.input.length === 1 ? object(request.input[0]) : {};
  if (plan.kind !== "offline-preview-only" || plan.source_run_id !== runId
    || typeof plan.repository !== "string" || plan.repository.toLowerCase() !== repository.toLowerCase()
    || typeof plan.base_sha !== "string" || !/^[0-9a-f]{40}$/.test(plan.base_sha)
    || [plan.plan_sha256, plan.snapshot_sha256, plan.readme_sha256].some(x => typeof x !== "string" || !hash.test(x))
    || typeof preview.readme !== "string" || new TextEncoder().encode(preview.readme).length !== plan.readme_bytes
    || !Number.isSafeInteger(plan.readme_bytes) || Number(plan.readme_bytes) < 1 || Number(plan.readme_bytes) > 4096
    || request.model !== "gpt-6-sol" || typeof request.instructions !== "string"
    || !Array.isArray(request.tools) || request.tools.length !== 0 || request.store !== false
    || request.max_output_tokens !== 512 || object(request.reasoning).effort !== "none" || request.service_tier !== "default"
    || input.role !== "user" || typeof input.content !== "string"
    || JSON.parse(input.content.slice("README source data (JSON string):\n".length)) !== preview.readme
    || !input.content.startsWith("README source data (JSON string):\n")
    || typeof limits.price_date !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(limits.price_date)
    || limits.generation_attempts !== 1 || limits.automatic_retries !== 0
    || limits.max_exact_input_tokens !== 2048 || limits.allowance_microusd !== 20000
    || limits.max_estimate_microusd !== 11264
    || limits.input_microusd_per_million !== 2750000 || limits.output_microusd_per_million !== 11000000
    || !Array.isArray(plan.unverified) || plan.unverified.some(x => typeof x !== "string")) {
    throw new Error("Invalid bounded preview");
  }
  if (preview.decision !== null) parseReceipt(preview.decision, String(plan.plan_sha256));
  return preview as unknown as ReadonlyPreview;
}
export function recordOnlyDecision(preview: ReadonlyPreview, decision: "approved" | "rejected", key: string) {
  if (preview.decision) throw new Error("Review already recorded");
  return { scope: reviewScope, repository: preview.plan.repository, plan_sha256: preview.plan.plan_sha256,
    decision, decision_key: key, acknowledge_record_only: true };
}
