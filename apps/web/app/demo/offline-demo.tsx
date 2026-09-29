"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";


type Decision = "approved" | "rejected" | null;

type DemoDecision = {
  event_id: string;
  decision: Exclude<Decision, null>;
  decision_key: string;
  actor: string;
  recorded_at: string;
  patch_hash: string;
  verdict_hash: string;
  authorizes_github_write: false;
};

type RunEvent = {
  event_id: string;
  run_id: string;
  sequence: number;
  schema_version: number;
  event_type: string;
  occurred_at: string;
  actor: string;
  payload: Record<string, unknown>;
};

type DemoCheck = {
  check_id: string;
  kind: string;
  status: string;
  required: boolean;
};

type DemoResponse = {
  mode: "local_deterministic";
  run: { run_id: string; state: string };
  events: RunEvent[];
  review: {
    patch: string;
    patch_hash: string;
    verdict_hash: string;
    verdict: string;
    changed_paths: string[];
    checks: DemoCheck[];
  };
  decision: DemoDecision | null;
  safety: {
    provider: "offline";
    model: string;
    model_calls: number;
    network_requests: number;
    cost_microusd: number;
    github_writes: number;
  };
};

type DemoStage = {
  title: string;
  event: RunEvent;
  description: string;
  evidence: string;
};

const apiBase = process.env.NEXT_PUBLIC_FORGE_API_URL ?? "http://localhost:8000";
const storedDemoRunKey = "forge.offline-demo.run-id";

function eventByType(events: RunEvent[], eventType: string): RunEvent {
  const event = events.find((item) => item.event_type === eventType);
  if (!event) throw new Error(`Backend evidence is missing ${eventType}`);
  return event;
}

function approvalEvent(events: RunEvent[]): RunEvent {
  const event = events.find(
    (item) => item.event_type === "state_changed" && item.payload.to_state === "AWAITING_APPROVAL"
  );
  if (!event) throw new Error("Backend evidence never reached human review");
  return event;
}

function buildStages(data: DemoResponse): DemoStage[] {
  const created = eventByType(data.events, "run_created");
  const snapshot = eventByType(data.events, "snapshot_ready");
  const indexed = eventByType(data.events, "repository_indexed");
  const agent = eventByType(data.events, "agent_started");
  const patch = eventByType(data.events, "patch_ready");
  const evaluation = eventByType(data.events, "evaluation_completed");
  const awaiting = approvalEvent(data.events);
  const brief = indexed.payload.brief as Record<string, unknown> | undefined;

  return [
    {
      title: "Create durable run",
      event: created,
      description: "Persist the objective and hard budgets before work begins.",
      evidence: `${data.run.run_id.slice(0, 8)} · durable SQLite record`
    },
    {
      title: "Snapshot repository",
      event: snapshot,
      description: "Copy the public fixture into an isolated, immutable workspace.",
      evidence: `${String(snapshot.payload.file_count)} files · ${String(snapshot.payload.snapshot_hash).slice(0, 12)}`
    },
    {
      title: "Index relevant code",
      event: indexed,
      description: "Build a repository manifest and select objective-relevant context.",
      evidence: `${String(brief?.indexed_file_count ?? "?")} indexed files · Python fixture`
    },
    {
      title: "Execute bounded tool loop",
      event: agent,
      description: "Reproduce the test, apply the checksum-bound patch, and rerun verification.",
      evidence: `${String(agent.payload.context_characters)} context characters · offline runtime`
    },
    {
      title: "Capture candidate patch",
      event: patch,
      description: "Store the exact diff as a content-addressed artifact.",
      evidence: `${data.review.changed_paths.length} file changed · ${data.review.patch_hash.slice(0, 12)}`
    },
    {
      title: "Verify independently",
      event: evaluation,
      description: "Rebuild from the original snapshot and evaluate the patch separately.",
      evidence: `${data.review.checks.length}/${data.review.checks.length} checks passed · ${data.review.verdict}`
    },
    {
      title: "Wait for human decision",
      event: awaiting,
      description: "Freeze the verified evidence until a reviewer decides.",
      evidence: `${data.run.state} · GitHub write blocked`
    }
  ];
}

function eventEvidence(event: RunEvent): string {
  const payload = event.payload;
  if (event.event_type === "state_changed") {
    return `${String(payload.from_state)} → ${String(payload.to_state)}`;
  }
  if (event.event_type === "snapshot_ready") {
    return `${String(payload.file_count)} files · ${String(payload.snapshot_hash).slice(0, 12)}`;
  }
  if (event.event_type === "repository_indexed") {
    const brief = payload.brief as Record<string, unknown> | undefined;
    return `${String(brief?.indexed_file_count ?? "?")} files indexed`;
  }
  if (event.event_type === "agent_started") {
    return `${String(payload.context_characters)} context characters`;
  }
  if (event.event_type === "agent_stopped") {
    return `${String(payload.tool_calls)} tools · ${String(payload.input_tokens)} input tokens · $0.00`;
  }
  if (event.event_type === "patch_ready") {
    return `${String((payload.changed_paths as unknown[] | undefined)?.length ?? 0)} file changed`;
  }
  if (event.event_type === "evaluation_completed") {
    return `${String((payload.checks as unknown[] | undefined)?.length ?? 0)} checks · ${String(payload.verdict)}`;
  }
  if (event.event_type === "workspace_destroyed") return "Isolated workspace removed";
  return event.actor;
}

function readableLabel(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (character) => character.toUpperCase());
}

async function responseJson(response: Response): Promise<DemoResponse> {
  if (response.ok) return (await response.json()) as DemoResponse;
  let message = `Local API returned ${response.status}`;
  try {
    const payload = (await response.json()) as { detail?: string };
    if (payload.detail) message = payload.detail;
  } catch {
    // The status is still useful when the server did not return JSON.
  }
  throw new Error(message);
}

export function OfflineDemo() {
  const [activeStage, setActiveStage] = useState(-1);
  const [running, setRunning] = useState(false);
  const [loading, setLoading] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [decisionBusy, setDecisionBusy] = useState<Decision>(null);
  const [decisionRequestKey, setDecisionRequestKey] = useState<string | null>(null);
  const [data, setData] = useState<DemoResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const stages = useMemo(() => (data ? buildStages(data) : []), [data]);
  const complete = stages.length > 0 && activeStage === stages.length - 1;
  const visibleEvents = useMemo(() => {
    if (!data || activeStage < 0) return [];
    if (complete) return [...data.events].reverse();
    const sequence = stages[activeStage]?.event.sequence ?? 0;
    return data.events.filter((event) => event.sequence <= sequence).reverse();
  }, [activeStage, complete, data, stages]);

  useEffect(() => {
    if (!running) return;
    if (activeStage >= stages.length - 1) {
      setRunning(false);
      return;
    }
    const timer = window.setTimeout(() => {
      setActiveStage((current) => current + 1);
    }, 520);
    return () => window.clearTimeout(timer);
  }, [activeStage, running, stages.length]);

  useEffect(() => {
    const runId = window.localStorage.getItem(storedDemoRunKey);
    if (!runId) return;
    let cancelled = false;
    setRestoring(true);
    fetch(`${apiBase}/demo/runs/${encodeURIComponent(runId)}`)
      .then(responseJson)
      .then((payload) => {
        if (cancelled) return;
        const restoredStages = buildStages(payload);
        setData(payload);
        setActiveStage(restoredStages.length - 1);
      })
      .catch((problem: unknown) => {
        if (cancelled) return;
        window.localStorage.removeItem(storedDemoRunKey);
        setError(problem instanceof Error ? problem.message : "The saved demo could not be restored.");
      })
      .finally(() => {
        if (!cancelled) setRestoring(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function startDemo() {
    setDecisionBusy(null);
    setDecisionRequestKey(null);
    setData(null);
    setError(null);
    setActiveStage(-1);
    setRunning(false);
    setLoading(true);
    try {
      const response = await fetch(`${apiBase}/demo/runs`, { method: "POST" });
      const payload = await responseJson(response);
      buildStages(payload);
      setData(payload);
      window.localStorage.setItem(storedDemoRunKey, payload.run.run_id);
      setActiveStage(0);
      setRunning(true);
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "The local API could not run the demo.");
    } finally {
      setLoading(false);
    }
  }

  function resetDemo() {
    setRunning(false);
    setLoading(false);
    setActiveStage(-1);
    setDecisionBusy(null);
    setDecisionRequestKey(null);
    setData(null);
    setError(null);
    window.localStorage.removeItem(storedDemoRunKey);
  }

  async function recordDecision(decision: Exclude<Decision, null>) {
    if (!data || decisionBusy) return;
    const requestKey = decisionRequestKey ?? `demo-review-${window.crypto.randomUUID()}`;
    setDecisionRequestKey(requestKey);
    setDecisionBusy(decision);
    setError(null);
    try {
      const response = await fetch(
        `${apiBase}/demo/runs/${encodeURIComponent(data.run.run_id)}/decision`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            decision,
            patch_hash: data.review.patch_hash,
            evaluation_verdict_hash: data.review.verdict_hash,
            decision_key: requestKey
          })
        }
      );
      const payload = await responseJson(response);
      setData(payload);
      window.localStorage.setItem(storedDemoRunKey, payload.run.run_id);
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "The decision could not be recorded.");
    } finally {
      setDecisionBusy(null);
    }
  }

  const decision = data?.decision?.decision ?? null;

  const status = decision
    ? decision === "approved"
      ? "Approved locally"
      : "Rejected locally"
    : restoring
      ? "Restoring durable review"
      : loading
      ? "Executing real workflow"
      : running
        ? "Replaying durable evidence"
        : complete
          ? "Awaiting your review"
          : "Ready to start";

  const patchLines = data?.review.patch.trimEnd().split("\n") ?? [];

  return (
    <main className="demo-page">
      <nav className="demo-nav" aria-label="Forge navigation">
        <Link href="/">Run console</Link>
        <div>
          <Link href="/benchmarks">Failure analysis</Link>
          <span>Offline demo</span>
        </div>
      </nav>

      <header className="demo-header">
        <div>
          <p className="eyebrow">Free backend-driven demo</p>
          <h1>See the real control loop. Spend nothing.</h1>
          <p className="lede">
            Run Forge&apos;s actual local store, sandbox, tool loop, artifact pipeline,
            and independent evaluator with a deterministic runtime instead of a paid model.
          </p>
        </div>
        <div className="demo-trust-card">
          <span className="demo-live-dot" aria-hidden="true" />
          <div>
            <strong>Offline-safe</strong>
            <p>No API key is read or required.</p>
          </div>
        </div>
      </header>

      <section className="demo-safety-strip" aria-label="Offline demo guarantees">
        <div><strong>$0.00</strong><span>API spend</span></div>
        <div><strong>{data?.safety.model_calls ?? 0}</strong><span>model calls</span></div>
        <div><strong>{data?.safety.network_requests ?? 0}</strong><span>external requests</span></div>
        <div><strong>Blocked</strong><span>GitHub writes</span></div>
      </section>

      <p className="demo-disclosure">
        <strong>What this is:</strong> a real local Forge run against a retired public
        smoke fixture. The runtime follows fixed scripted steps, so this proves the
        engineering workflow—not AI quality or benchmark performance.
      </p>

      <section className="demo-workspace">
        <div className="demo-run-panel">
          <div className="demo-panel-heading">
            <div>
              <span className="label">Public smoke task</span>
              <h2>Normalize user-entered status strings</h2>
            </div>
            <span className={`demo-status ${running || loading ? "is-running" : ""}`} aria-live="polite">
              {status}
            </span>
          </div>

          <div className="demo-task-card">
            <div>
              <span>Execution</span>
              <strong>Real local backend</strong>
            </div>
            <div>
              <span>Runtime</span>
              <strong>Deterministic · zero tokens</strong>
            </div>
            <p>
              Make status validation accept surrounding whitespace and mixed-case input
              without changing the public function contract.
            </p>
          </div>

          {error ? (
            <div className="demo-backend-error" role="alert">
              <strong>Local API needs attention</strong>
              <p>{error}</p>
              <span>Restart the Forge API, then choose “Try backend again.”</span>
            </div>
          ) : null}

          <div className="demo-actions">
            <button type="button" onClick={startDemo} disabled={running || loading || restoring}>
              {loading ? "Executing locally…" : error ? "Try backend again" : data ? "Run another real demo" : "Run real offline demo"}
            </button>
            <button type="button" className="secondary" onClick={resetDemo} disabled={loading || restoring}>
              Reset
            </button>
          </div>

          <ol className="demo-stage-list">
            {(stages.length ? stages : [
              "Create durable run",
              "Snapshot repository",
              "Index relevant code",
              "Execute bounded tool loop",
              "Capture candidate patch",
              "Verify independently",
              "Wait for human decision"
            ]).map((stage, index) => {
              const populated = typeof stage !== "string";
              const state = populated
                ? index < activeStage ? "complete" : index === activeStage ? "active" : "pending"
                : "pending";
              return (
                <li key={populated ? stage.event.event_id : stage} className={`demo-stage demo-stage-${state}`}>
                  <span className="demo-stage-marker" aria-hidden="true">
                    {state === "complete" ? "✓" : index + 1}
                  </span>
                  <div>
                    <strong>{populated ? stage.title : stage}</strong>
                    {populated ? <p>{stage.description}</p> : null}
                    {populated && index <= activeStage ? <code>{stage.evidence}</code> : null}
                  </div>
                </li>
              );
            })}
          </ol>
        </div>

        <aside className="demo-event-panel" aria-label="Durable event evidence">
          <div className="demo-panel-heading compact">
            <div>
              <span className="label">Real event stream</span>
              <h2>Durable evidence</h2>
            </div>
            <code>{data ? data.run.run_id.slice(0, 13) : "not started"}</code>
          </div>
          {visibleEvents.length === 0 ? (
            <div className="demo-empty-state">
              <span>◇</span>
              <p>Run the demo to create and replay persisted backend events.</p>
            </div>
          ) : (
            <ul className="demo-event-list" aria-live="polite">
              {visibleEvents.map((event) => (
                <li key={event.event_id}>
                  <span>{String(event.sequence).padStart(2, "0")}</span>
                  <div><code>{event.event_type}</code><small>{eventEvidence(event)}</small></div>
                </li>
              ))}
            </ul>
          )}
        </aside>
      </section>

      {complete && data ? (
        <section className="demo-review" aria-live="polite">
          <div className="demo-patch-panel">
            <div className="demo-panel-heading compact">
              <div>
                <span className="label">Content-addressed patch</span>
                <h2>One-line normalization</h2>
              </div>
              <span className="demo-file-count">{data.review.changed_paths.length} file changed</span>
            </div>
            <pre className="demo-diff" aria-label="Candidate code change">
              {patchLines.map((line, index) => {
                const kind = line.startsWith("+") && !line.startsWith("+++")
                  ? "add"
                  : line.startsWith("-") && !line.startsWith("---")
                    ? "remove"
                    : line.startsWith("diff") || line.startsWith("---") || line.startsWith("+++") || line.startsWith("@@")
                      ? "meta"
                      : "plain";
                return <code key={`${index}-${line}`} className={`demo-diff-${kind}`}>{line || " "}</code>;
              })}
            </pre>
          </div>

          <div className="demo-approval-panel">
            <span className="label">Independent evaluation</span>
            <h2>Review the real evidence</h2>
            <ul className="demo-check-list">
              {data.review.checks.map((check) => (
                <li key={check.check_id}>
                  <span>✓</span>
                  <strong>{readableLabel(check.check_id)}</strong>
                  <small>{readableLabel(check.status)}</small>
                </li>
              ))}
            </ul>

            {decision ? (
              <div className={`demo-decision demo-decision-${decision}`}>
                <strong>{decision === "approved" ? "Patch approved locally" : "Patch rejected locally"}</strong>
                <p>
                  The backend audit trail recorded this evidence-bound review. It creates
                  no branch, commit, pull request, publication permission, or external write.
                </p>
                <small>Recorded by {data.decision?.actor} · survives refresh</small>
                <button type="button" className="secondary" onClick={startDemo}>Run a new demo</button>
              </div>
            ) : (
              <div className="demo-review-actions">
                <button type="button" disabled={decisionBusy !== null} onClick={() => recordDecision("approved")}>
                  {decisionBusy === "approved" ? "Recording…" : "Approve locally"}
                </button>
                <button type="button" className="demo-reject" disabled={decisionBusy !== null} onClick={() => recordDecision("rejected")}>
                  {decisionBusy === "rejected" ? "Recording…" : "Reject"}
                </button>
                <p>The backend stores one immutable decision. GitHub publishing remains disabled.</p>
              </div>
            )}
          </div>
        </section>
      ) : null}
    </main>
  );
}
