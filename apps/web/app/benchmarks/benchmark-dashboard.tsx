"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ReviewerControls, useApiClient } from "../api-session";


type RateMetric = {
  numerator: number;
  denominator: number;
  rate: number | null;
  wilson_95: [number | null, number | null];
};

type Distribution = { median: number | null; p95: number | null };

type BenchmarkSummary = {
  benchmark_version: string;
  experiment_id: string;
  configuration_digest: string;
  code_revision: string;
  task_set_digest: string;
  record_set_digest: string;
  counts: {
    records: number;
    valid: number;
    scored: number;
    solved: number;
    infrastructure_invalid: number;
    task_invalid: number;
  };
  metrics: {
    verified_solve_rate: RateMetric;
    first_attempt_solve_rate: RateMetric;
    regression_free_rate: RateMetric;
    infrastructure_failure_rate: RateMetric;
    duration_ms: Distribution;
    cost_microusd: Distribution;
    tool_calls: Distribution;
    cost_per_attempted_task_microusd: number | null;
    cost_per_verified_solve_microusd: number | null;
  };
  category_breakdown: Record<
    string,
    { records: number; solved: number; verified_solve_rate: RateMetric }
  >;
  language_distribution: Record<string, number>;
  split_distribution: Record<string, number>;
  failure_distribution: Record<string, number>;
  release_gates: Array<{ gate: string; passed: boolean; description: string }>;
  release_ready: boolean;
};

type BenchmarkRecord = {
  task_id: string;
  task_split: string;
  category: string;
  language: string;
  run_id: string;
  verdict: string;
  failure_code: string | null;
  regression_free: boolean;
  patch_attempts: number;
  duration_ms: number;
  cost_microusd: number;
  tool_calls: number;
};

type DashboardResponse = {
  source: { classification: "synthetic" | "development" | "release"; record_count: number };
  filters: { category: string | null; split: string | null };
  available: { categories: string[]; splits: string[] };
  summary: BenchmarkSummary | null;
  summary_digest: string | null;
  full_summary: BenchmarkSummary;
  full_summary_digest: string;
  records: BenchmarkRecord[];
};

function percent(metric: RateMetric): string {
  return metric.rate === null ? "Not available" : `${(metric.rate * 100).toFixed(1)}%`;
}

function interval(metric: RateMetric): string {
  const [lower, upper] = metric.wilson_95;
  if (lower === null || upper === null) return "No scored denominator";
  return `95% Wilson interval ${(lower * 100).toFixed(1)}–${(upper * 100).toFixed(1)}%`;
}

function dollars(microusd: number | null): string {
  return microusd === null ? "Not available" : `$${(microusd / 1_000_000).toFixed(4)}`;
}

function duration(milliseconds: number | null): string {
  if (milliseconds === null) return "Not available";
  if (milliseconds < 60_000) return `${(milliseconds / 1000).toFixed(1)}s`;
  return `${(milliseconds / 60_000).toFixed(1)}m`;
}

function label(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (character) => character.toUpperCase());
}

async function responseJson(response: Response): Promise<DashboardResponse> {
  if (response.ok) return (await response.json()) as DashboardResponse;
  let message = `Benchmark API returned ${response.status}`;
  try {
    const payload = (await response.json()) as { detail?: string };
    if (payload.detail) message = payload.detail;
  } catch {
    // The status remains useful when the response has no JSON body.
  }
  throw new Error(message);
}

export function BenchmarkDashboard() {
  const api = useApiClient();
  const [category, setCategory] = useState("");
  const [taskSplit, setTaskSplit] = useState("");
  const [data, setData] = useState<DashboardResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!api.ready) { setData(null); setLoading(false); return; }
    const controller = new AbortController();
    const query = new URLSearchParams();
    if (category) query.set("category", category);
    if (taskSplit) query.set("split", taskSplit);
    setLoading(true);
    setError(null);
    api.request(`/benchmarks/latest${query.size ? `?${query}` : ""}`, {
      signal: controller.signal
    })
      .then(responseJson)
      .then((payload) => { if (!controller.signal.aborted) setData(payload); })
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return;
        setData(null);
        setError(caught instanceof Error ? caught.message : "Unable to load benchmark evidence");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [api, category, taskSplit]);

  const records = useMemo(
    () =>
      [...(data?.records ?? [])].sort((left, right) => {
        const leftSolved = left.verdict === "solved" ? 1 : 0;
        const rightSolved = right.verdict === "solved" ? 1 : 0;
        return leftSolved - rightSolved || left.task_id.localeCompare(right.task_id);
      }),
    [data]
  );
  const classification = data?.source.classification;
  const classificationMessage =
    classification === "synthetic"
      ? "Synthetic development evidence — never cite these values as Forge performance."
      : classification === "release" && data?.full_summary.release_ready
        ? "Release evidence — every encoded result-set gate passes."
        : classification === "release"
          ? "Release classification is blocked because one or more evidence gates remain open."
          : "Development evidence — useful for diagnosis, not a released performance claim.";

  return (
    <main className="benchmark-page">
      <nav className="benchmark-nav" aria-label="Forge navigation">
        <Link href="/">Run console</Link>
        <div>
          <Link href="/demo">Offline demo</Link>
          <ReviewerControls />
          <span>Forge evidence</span>
        </div>
      </nav>
      <header className="benchmark-header">
        <div>
          <p className="eyebrow">Milestone 7</p>
          <h1>Failure analysis</h1>
          <p className="lede">
            Diagnose verified outcomes, invalidations, cost, and failure modes from versioned run evidence.
          </p>
        </div>
        {data ? (
          <div className={`evidence-classification evidence-${classification}`}>
            <strong>{label(classification ?? "development")}</strong>
            <span>{data.source.record_count} source records</span>
          </div>
        ) : null}
      </header>
      {!api.ready ? <section className="benchmark-state" role="status">Sign in as an authorized reviewer to load benchmark evidence.</section> : null}

      {data ? <p className={`evidence-notice evidence-${classification}`}>{classificationMessage}</p> : null}

      <section className="benchmark-controls" aria-label="Benchmark filters">
        <div>
          <span className="label">View scope</span>
          <p>Every card, chart, and record below uses the same selected population.</p>
        </div>
        <label>
          Category
          <select value={category} onChange={(event) => setCategory(event.target.value)}>
            <option value="">All categories</option>
            {(data?.available.categories ?? []).map((item) => (
              <option key={item} value={item}>{label(item)}</option>
            ))}
          </select>
        </label>
        <label>
          Split
          <select value={taskSplit} onChange={(event) => setTaskSplit(event.target.value)}>
            <option value="">All splits</option>
            {(data?.available.splits ?? []).map((item) => (
              <option key={item} value={item}>{label(item)}</option>
            ))}
          </select>
        </label>
      </section>

      {loading ? <section className="benchmark-state">Loading reviewed benchmark evidence…</section> : null}
      {!loading && error ? (
        <section className="benchmark-state benchmark-state-error" role="alert">
          <h2>Benchmark evidence unavailable</h2>
          <p>{error}</p>
          <span>Configure a reviewed JSONL record set to activate this dashboard.</span>
        </section>
      ) : null}
      {!loading && !error && data && !data.summary ? (
        <section className="benchmark-state">
          <h2>No records match this view</h2>
          <p>Change or clear the category and split filters.</p>
        </section>
      ) : null}

      {!loading && !error && data?.summary ? (
        <DashboardBody data={data} records={records} filtered={Boolean(category || taskSplit)} />
      ) : null}
    </main>
  );
}

function DashboardBody({
  data,
  records,
  filtered
}: {
  data: DashboardResponse;
  records: BenchmarkRecord[];
  filtered: boolean;
}) {
  const summary = data.summary as BenchmarkSummary;
  const exceptions = records.filter((record) => record.verdict !== "solved");
  const failures = Object.entries(summary.failure_distribution).sort(
    ([left], [right]) => left.localeCompare(right)
  );
  const maximumFailure = Math.max(1, ...failures.map(([, count]) => count));

  return (
    <div className="benchmark-content">
      <section className="benchmark-kpis" aria-label="Benchmark headline metrics">
        <MetricCard
          role="Outcome"
          title="Verified solve rate"
          value={percent(summary.metrics.verified_solve_rate)}
          detail={interval(summary.metrics.verified_solve_rate)}
        />
        <MetricCard
          role="Population"
          title="Scored tasks"
          value={`${summary.counts.scored}`}
          detail={`${summary.counts.solved} solved · ${summary.counts.records} records supplied`}
        />
        <MetricCard
          role="Guardrail"
          title="Regression-free"
          value={percent(summary.metrics.regression_free_rate)}
          detail={`${summary.metrics.regression_free_rate.numerator} of ${summary.metrics.regression_free_rate.denominator} scored tasks`}
        />
        <MetricCard
          role="Reliability"
          title="Infrastructure invalid"
          value={percent(summary.metrics.infrastructure_failure_rate)}
          detail={`${summary.counts.infrastructure_invalid} invalidated attempt${summary.counts.infrastructure_invalid === 1 ? "" : "s"}`}
        />
        <MetricCard
          role="Efficiency"
          title="Cost per verified solve"
          value={dollars(summary.metrics.cost_per_verified_solve_microusd)}
          detail={`Median attempt ${dollars(summary.metrics.cost_microusd.median)} · p95 ${dollars(summary.metrics.cost_microusd.p95)}`}
        />
        <MetricCard
          role="Latency"
          title="Time to verdict"
          value={duration(summary.metrics.duration_ms.median)}
          detail={`Median · p95 ${duration(summary.metrics.duration_ms.p95)}`}
        />
      </section>

      <section className="benchmark-analysis-grid">
        <article className="benchmark-panel">
          <div className="benchmark-panel-heading">
            <div><span className="label">Comparison</span><h2>Performance by category</h2></div>
            <span>{summary.counts.scored} scored</span>
          </div>
          <div className="category-table" role="table" aria-label="Category solve rates">
            {Object.entries(summary.category_breakdown).map(([category, item]) => {
              const rate = item.verified_solve_rate.rate ?? 0;
              return (
                <div className="category-row" role="row" key={category}>
                  <strong role="cell">{label(category)}</strong>
                  <div className="rate-track" role="cell" aria-label={`${percent(item.verified_solve_rate)} solved`}>
                    <span style={{ width: `${rate * 100}%` }} />
                  </div>
                  <span role="cell">{percent(item.verified_solve_rate)}</span>
                  <small role="cell">{item.solved}/{item.records}</small>
                </div>
              );
            })}
          </div>
        </article>

        <article className="benchmark-panel">
          <div className="benchmark-panel-heading">
            <div><span className="label">Diagnostic</span><h2>Failure distribution</h2></div>
            <span>{failures.reduce((total, [, count]) => total + count, 0)} failures</span>
          </div>
          {failures.length ? (
            <div className="failure-bars">
              {failures.map(([failure, count]) => (
                <div className="failure-row" key={failure}>
                  <div><strong>{label(failure)}</strong><span>{count}</span></div>
                  <div className="failure-track" aria-label={`${count} ${label(failure)} failures`}>
                    <span style={{ width: `${(count / maximumFailure) * 100}%` }} />
                  </div>
                </div>
              ))}
            </div>
          ) : <p className="panel-empty">No failures exist in this selected population.</p>}
        </article>
      </section>

      <section className="benchmark-panel benchmark-records">
        <div className="benchmark-panel-heading">
          <div><span className="label">Task evidence</span><h2>Attempts requiring investigation</h2></div>
          <span>{exceptions.length} exceptions</span>
        </div>
        {exceptions.length ? <div className="records-scroll">
          <table>
            <thead><tr><th>Task</th><th>Scope</th><th>Verdict</th><th>Failure</th><th>Attempts</th><th>Duration</th><th>Cost</th></tr></thead>
            <tbody>
              {exceptions.map((record) => (
                <tr key={record.task_id}>
                  <td><strong>{record.task_id}</strong><small>{record.run_id}</small></td>
                  <td>{label(record.category)}<small>{label(record.task_split)} / {label(record.language)}</small></td>
                  <td><span className={`record-verdict verdict-${record.verdict}`}>{label(record.verdict)}</span></td>
                  <td>{record.failure_code ? label(record.failure_code) : "—"}</td>
                  <td>{record.patch_attempts} patch / {record.tool_calls} tools</td>
                  <td>{duration(record.duration_ms)}</td>
                  <td>{dollars(record.cost_microusd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div> : <p className="panel-empty">No attempts in this selected population require investigation.</p>}
      </section>

      <section className="benchmark-analysis-grid benchmark-footer-grid">
        <article className="benchmark-panel">
          <div className="benchmark-panel-heading">
            <div><span className="label">Release evidence</span><h2>Full-set gates</h2></div>
            <span>{data.full_summary.release_ready ? "Ready" : "Open"}</span>
          </div>
          {filtered ? <p className="scope-note">These gates always use the full unfiltered record set.</p> : null}
          <ul className="gate-list">
            {data.full_summary.release_gates.map((gate) => (
              <li key={gate.gate} className={gate.passed ? "gate-pass" : "gate-open"}>
                <span aria-hidden="true">{gate.passed ? "✓" : "○"}</span>
                <div><strong>{gate.passed ? "Pass" : "Open"}</strong><p>{gate.description}</p></div>
              </li>
            ))}
          </ul>
        </article>
        <article className="benchmark-panel provenance-panel">
          <div className="benchmark-panel-heading">
            <div><span className="label">Provenance</span><h2>Reproduction identity</h2></div>
          </div>
          <dl>
            <div><dt>Benchmark</dt><dd>{summary.benchmark_version}</dd></div>
            <div><dt>Experiment</dt><dd>{summary.experiment_id}</dd></div>
            <div><dt>Code revision</dt><dd><code>{summary.code_revision}</code></dd></div>
            <div><dt>Configuration</dt><dd><code>{summary.configuration_digest}</code></dd></div>
            <div><dt>Selected summary</dt><dd><code>{data.summary_digest}</code></dd></div>
            <div><dt>Full evidence set</dt><dd><code>{data.full_summary_digest}</code></dd></div>
          </dl>
        </article>
      </section>
    </div>
  );
}

function MetricCard({ role, title, value, detail }: { role: string; title: string; value: string; detail: string }) {
  return (
    <article className="metric-card">
      <span className="label">{role}</span>
      <h2>{value}</h2>
      <strong>{title}</strong>
      <p>{detail}</p>
    </article>
  );
}
