"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";


type Decision = "approved" | "rejected" | null;

type DemoStage = {
  title: string;
  event: string;
  description: string;
  evidence: string;
};

const demoStages: DemoStage[] = [
  {
    title: "Snapshot repository",
    event: "source.snapshot.created",
    description: "Pin a read-only source revision before any investigation begins.",
    evidence: "demo/status-normalizer @ 8f31a2c"
  },
  {
    title: "Index relevant code",
    event: "repository.index.completed",
    description: "Map the small fixture and select the function related to the task.",
    evidence: "3 files indexed · status.py selected"
  },
  {
    title: "Prepare bounded context",
    event: "agent.context.prepared",
    description: "Construct the exact context an engineering agent would receive.",
    evidence: "task + source + public test · no hidden tests"
  },
  {
    title: "Reproduce failure",
    event: "test.public.failed",
    description: "Confirm the existing implementation rejects whitespace and mixed case.",
    evidence: "1 failed · expected active, received ' Active '"
  },
  {
    title: "Apply candidate patch",
    event: "patch.candidate.created",
    description: "Apply a minimal change inside the simulated isolated workspace.",
    evidence: "1 file changed · 1 insertion · 1 deletion"
  },
  {
    title: "Verify independently",
    event: "evaluation.completed",
    description: "Replay the checks as an evaluator, separate from patch creation.",
    evidence: "4/4 checks passed · regression free"
  },
  {
    title: "Wait for human decision",
    event: "review.requested",
    description: "Freeze the exact patch and evidence until a reviewer decides.",
    evidence: "GitHub write blocked · approval required"
  }
];

const checks = [
  ["Public regression", "Passed"],
  ["Acceptance smoke", "Passed"],
  ["Patch policy", "Passed"],
  ["Clean replay", "Passed"]
] as const;

const patchLines = [
  { kind: "meta", value: "diff --git a/status.py b/status.py" },
  { kind: "meta", value: "--- a/status.py" },
  { kind: "meta", value: "+++ b/status.py" },
  { kind: "meta", value: "@@ -1,2 +1,2 @@" },
  { kind: "plain", value: " def normalize_status(value: str) -> str:" },
  { kind: "remove", value: "-    return value" },
  { kind: "add", value: "+    return value.strip().lower()" }
] as const;

export function OfflineDemo() {
  const [activeStage, setActiveStage] = useState(-1);
  const [running, setRunning] = useState(false);
  const [decision, setDecision] = useState<Decision>(null);

  const complete = activeStage === demoStages.length - 1;
  const visibleEvents = useMemo(
    () => demoStages.slice(0, activeStage + 1).reverse(),
    [activeStage]
  );

  useEffect(() => {
    if (!running) return;
    if (activeStage >= demoStages.length - 1) {
      setRunning(false);
      return;
    }

    const timer = window.setTimeout(() => {
      setActiveStage((current) => current + 1);
    }, 650);

    return () => window.clearTimeout(timer);
  }, [activeStage, running]);

  function startDemo() {
    setDecision(null);
    setActiveStage(0);
    setRunning(true);
  }

  function resetDemo() {
    setRunning(false);
    setActiveStage(-1);
    setDecision(null);
  }

  const status = decision
    ? decision === "approved"
      ? "Approved locally"
      : "Rejected locally"
    : running
      ? "Simulating run"
      : complete
        ? "Awaiting your review"
        : "Ready to start";

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
          <p className="eyebrow">Free product tour</p>
          <h1>See the control loop. Spend nothing.</h1>
          <p className="lede">
            Walk through a deterministic example of Forge investigating, patching,
            verifying, and requesting approval. Everything stays in this browser.
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
        <div><strong>0</strong><span>model calls</span></div>
        <div><strong>0</strong><span>network requests</span></div>
        <div><strong>Blocked</strong><span>GitHub writes</span></div>
      </section>

      <p className="demo-disclosure">
        <strong>What this is:</strong> a fixed, front-end product tour using a retired
        public smoke example. It demonstrates the workflow, not AI quality or benchmark performance.
      </p>

      <section className="demo-workspace">
        <div className="demo-run-panel">
          <div className="demo-panel-heading">
            <div>
              <span className="label">Prepared task</span>
              <h2>Normalize user-entered status strings</h2>
            </div>
            <span className={`demo-status ${running ? "is-running" : ""}`} aria-live="polite">
              {status}
            </span>
          </div>

          <div className="demo-task-card">
            <div>
              <span>Repository</span>
              <strong>forge-demo/status-normalizer</strong>
            </div>
            <div>
              <span>Fixture</span>
              <strong>Retired public smoke</strong>
            </div>
            <p>
              Make status validation accept surrounding whitespace and mixed-case input
              without changing the public function contract.
            </p>
          </div>

          <div className="demo-actions">
            <button type="button" onClick={startDemo} disabled={running}>
              {activeStage < 0 ? "Start offline demo" : "Replay demo"}
            </button>
            <button type="button" className="secondary" onClick={resetDemo} disabled={running && activeStage === 0}>
              Reset
            </button>
          </div>

          <ol className="demo-stage-list">
            {demoStages.map((stage, index) => {
              const state = index < activeStage ? "complete" : index === activeStage ? "active" : "pending";
              return (
                <li key={stage.event} className={`demo-stage demo-stage-${state}`}>
                  <span className="demo-stage-marker" aria-hidden="true">
                    {state === "complete" ? "✓" : index + 1}
                  </span>
                  <div>
                    <strong>{stage.title}</strong>
                    <p>{stage.description}</p>
                    {index <= activeStage ? <code>{stage.evidence}</code> : null}
                  </div>
                </li>
              );
            })}
          </ol>
        </div>

        <aside className="demo-event-panel" aria-label="Durable event preview">
          <div className="demo-panel-heading compact">
            <div>
              <span className="label">Event stream</span>
              <h2>Durable evidence</h2>
            </div>
            <code>demo-local-001</code>
          </div>
          {visibleEvents.length === 0 ? (
            <div className="demo-empty-state">
              <span>◇</span>
              <p>Start the demo to watch append-only run events appear.</p>
            </div>
          ) : (
            <ul className="demo-event-list" aria-live="polite">
              {visibleEvents.map((stage, index) => (
                <li key={stage.event}>
                  <span>{String(visibleEvents.length - index).padStart(2, "0")}</span>
                  <div><code>{stage.event}</code><small>{stage.evidence}</small></div>
                </li>
              ))}
            </ul>
          )}
        </aside>
      </section>

      {complete ? (
        <section className="demo-review" aria-live="polite">
          <div className="demo-patch-panel">
            <div className="demo-panel-heading compact">
              <div>
                <span className="label">Candidate patch</span>
                <h2>One-line normalization</h2>
              </div>
              <span className="demo-file-count">1 file changed</span>
            </div>
            <pre className="demo-diff" aria-label="Candidate code change">
              {patchLines.map((line) => (
                <code key={line.value} className={`demo-diff-${line.kind}`}>{line.value}</code>
              ))}
            </pre>
          </div>

          <div className="demo-approval-panel">
            <span className="label">Independent evaluation</span>
            <h2>Review the evidence</h2>
            <ul className="demo-check-list">
              {checks.map(([name, result]) => (
                <li key={name}><span>✓</span><strong>{name}</strong><small>{result}</small></li>
              ))}
            </ul>

            {decision ? (
              <div className={`demo-decision demo-decision-${decision}`}>
                <strong>{decision === "approved" ? "Patch approved locally" : "Patch rejected locally"}</strong>
                <p>
                  This demo recorded your decision only in page memory. No branch, commit,
                  pull request, or external write was created.
                </p>
                <button type="button" className="secondary" onClick={startDemo}>Replay demo</button>
              </div>
            ) : (
              <div className="demo-review-actions">
                <button type="button" onClick={() => setDecision("approved")}>Approve exact patch</button>
                <button type="button" className="demo-reject" onClick={() => setDecision("rejected")}>Reject</button>
                <p>Both choices are local and reversible in this product tour.</p>
              </div>
            )}
          </div>
        </section>
      ) : null}
    </main>
  );
}
