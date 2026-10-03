"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ReviewerControls, useApiClient, useApiSession } from "../api-session";
import { ApiAccessError } from "../../lib/api-client";
import { parsePreview, parseReceipt, previewPath, recordOnlyDecision } from "../../lib/readonly-preview";
import type { ReadonlyPreview } from "../../lib/readonly-preview";

const dollars = (microusd: number) => `$${(microusd / 1_000_000).toFixed(6)}`;

export function ReadonlyPilotReview({ initialRun, initialRepository }: { initialRun: string; initialRepository: string }) {
  const auth = useApiSession();
  const api = useApiClient();
  const [runId, setRunId] = useState(initialRun);
  const [repository, setRepository] = useState(initialRepository);
  const [preview, setPreview] = useState<ReadonlyPreview | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [error, setError] = useState("");
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), [api]);
  const signedIn = auth.status === "signed-in" && !auth.accessDenied && api.ready;

  function clearPreview() {
    setPreview(null); setAcknowledged(false); setUncertain(false); setError("");
  }
  async function loadPreview() {
    if (!signedIn || active.current) return;
    clearPreview();
    const controller = new AbortController();
    active.current = controller; setBusy(true);
    try {
      const response = await api.request(previewPath(runId.trim(), repository.trim()), {
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(30_000)])
      }, true);
      const value = parsePreview(await response.json(), runId.trim(), repository.trim());
      if (!controller.signal.aborted) setPreview(value);
    } catch (cause) {
      if (!controller.signal.aborted) setError(cause instanceof ApiAccessError ? cause.message
        : "Preview unavailable. Check the completed smoke-test run ID and owner/repository, then reload.");
    } finally {
      if (!controller.signal.aborted) { active.current = null; setBusy(false); }
    }
  }
  async function record(decision: "approved" | "rejected") {
    if (!signedIn || !preview || preview.decision || !acknowledged || uncertain || active.current) return;
    const controller = new AbortController();
    active.current = controller; setBusy(true); setError("");
    try {
      const response = await api.request(`/readonly-previews/${preview.plan.source_run_id}/decision`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(recordOnlyDecision(preview, decision, crypto.randomUUID())),
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(30_000)])
      }, true);
      const receipt = parseReceipt(await response.json(), preview.plan.plan_sha256);
      if (!controller.signal.aborted) { setPreview({ ...preview, decision: receipt }); setAcknowledged(false); }
    } catch (cause) {
      if (!controller.signal.aborted) {
        setUncertain(true); setAcknowledged(false);
        setError(cause instanceof ApiAccessError && [401, 403].includes(cause.status) ? cause.message
          : "Recording was not confirmed. Load the preview again to check for an existing receipt; this page will not retry automatically.");
      }
    } finally {
      if (!controller.signal.aborted) { active.current = null; setBusy(false); }
    }
  }
  const plan = preview?.plan;
  return <main>
    <header className="hero">
      <nav className="home-nav" aria-label="Forge navigation"><Link href="/">← Back to Forge</Link></nav>
      <p className="eyebrow">Free · record-only review</p>
      <h1>Review, without running.</h1>
      <p className="lede">Inspect the captured README and exact proposed AI request. Record your decision with your signed-in identity. No AI request will be sent.</p>
    </header>
    <section className="console readonly-review" aria-label="Read-only pilot review">
      <div className="console-auth"><span className="label">Reviewer access</span><ReviewerControls /></div>
      <div className="run-form">
        <p className="readonly-safety">AI execution is disabled. Approval here does not authorize source upload, spending, file changes, or GitHub writes.</p>
        {!signedIn && <p role="status">Sign in with an approved reviewer account to load or record a review. No anonymous development bypass is available.</p>}
        <label htmlFor="source-run">Completed README smoke-test run ID</label>
        <input id="source-run" value={runId} disabled={!signedIn || busy} autoComplete="off"
          onChange={event => { setRunId(event.target.value); clearPreview(); }} placeholder="Run UUID" />
        <label htmlFor="source-repository">Expected disposable repository</label>
        <input id="source-repository" value={repository} disabled={!signedIn || busy} autoComplete="off"
          onChange={event => { setRepository(event.target.value); clearPreview(); }} placeholder="owner/repository" />
        <button type="button" disabled={!signedIn || busy} onClick={() => void loadPreview()}>{busy ? "Please wait…" : "Load preview / restore receipt"}</button>
        {error && <p className="compact-error" role="alert">{error}</p>}
      </div>
      {preview && plan && <>
        <section className="run-form" aria-labelledby="source-heading">
          <h2 id="source-heading">Immutable source</h2>
          <dl className="readonly-evidence">
            <dt>Repository</dt><dd>{plan.repository}</dd><dt>Commit</dt><dd><code>{plan.base_sha}</code></dd>
            <dt>Snapshot fingerprint</dt><dd><code>{plan.snapshot_sha256}</code></dd>
            <dt>README fingerprint</dt><dd><code>{plan.readme_sha256}</code></dd>
            <dt>Request fingerprint</dt><dd><code>{plan.plan_sha256}</code></dd>
          </dl>
          <h3>Captured README · {plan.readme_bytes} bytes</h3>
          <p>Untrusted repository text, shown as data only.</p><pre>{preview.readme}</pre>
        </section>
        <section className="run-form" aria-labelledby="request-heading">
          <h2 id="request-heading">Exact proposed request · not sent</h2>
          <pre>{JSON.stringify(plan.request, null, 2)}</pre>
          <h3>Conditional estimate, not a billing guarantee</h3>
          <p>Price assumptions dated {plan.limits.price_date}. Proposed allowance: {dollars(plan.limits.allowance_microusd)}. Conditional maximum estimate: {dollars(plan.limits.max_estimate_microusd)}.</p>
          <p>Input ceiling: {plan.limits.max_exact_input_tokens} exact tokens; output ceiling: {plan.request.max_output_tokens}. One proposed generation, zero automatic retries. No token-count request or generation has been made by this screen.</p>
          <details><summary>Price assumptions and limits</summary><pre>{JSON.stringify(plan.limits, null, 2)}</pre></details>
          <h3>Still unverified</h3><ul>{plan.unverified.map(item => <li key={item}>{item}</li>)}</ul>
          <p>Future live use requires separate explicit upload and spending consent plus billing, current-price, model and exact-count checks. This record cannot be used as that consent.</p>
        </section>
        <section className="run-form" aria-labelledby="decision-heading">
          <h2 id="decision-heading">Record-only decision</h2>
          {preview.decision ? <div role="status">
            <h3>{preview.decision.decision === "approved" ? "Approval recorded — not executed" : "Rejection recorded — not executed"}</h3>
            <p>Reviewer: {preview.decision.actor}</p><p>Recorded: {preview.decision.created_at}</p>
            <p>Receipt: <code>{preview.decision.event_id}</code></p>
            <p>The completed source run is unchanged. Paid execution remains disabled.</p>
          </div> : <>
            <label className="toggle-row"><input type="checkbox" checked={acknowledged} disabled={busy || uncertain || !signedIn}
              onChange={event => setAcknowledged(event.target.checked)} />
              <span>I reviewed the exact source and request. I understand this records a review only and does not authorize execution or spending.</span></label>
            <div className="demo-actions">
              <button type="button" disabled={!signedIn || busy || !acknowledged || uncertain} onClick={() => void record("approved")}>Record approval only</button>
              <button type="button" className="secondary" disabled={!signedIn || busy || !acknowledged || uncertain} onClick={() => void record("rejected")}>Record rejection</button>
            </div>
            <p>One final record per exact request. Reloading restores the receipt; changing the request requires a new review.</p>
          </>}
        </section>
      </>}
    </section>
  </main>;
}
