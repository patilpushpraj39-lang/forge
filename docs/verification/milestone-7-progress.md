# Milestone 7 Progress: Benchmark and Hardening

Status: reporting and failure-analysis foundation complete locally; task
expansion, experiments, and tagged reproduction remain.

## Implemented evidence

- A strict versioned result record captures the task, split, category,
  experiment, product revision, configuration, evaluation manifest, verdict,
  failure, regression status, attempts, duration, cost, and tool count.
- Mixed configurations, duplicate tasks, malformed hashes, unknown fields, and
  inconsistent verdicts are rejected before aggregation.
- Solver failures, infrastructure invalidations, and invalid tasks have
  explicit, different denominators.
- The summary reports solve rate with a Wilson interval, first-attempt solve
  rate, regression-free rate, infrastructure failure rate, median and p95
  resource metrics, cost efficiency, composition, and failure distribution.
- Canonical task-set, record-set, and summary digests make the report
  reproducible regardless of input order.
- Release-evidence gates encode the 20-task, five-category, ten-holdout,
  zero-invalidation minimum without claiming that those tasks exist yet.
- A command-line builder emits both machine-readable JSON and a portfolio-ready
  Markdown card.
- A durable-run exporter derives record metrics and verdicts from ordered Forge
  events, rejects conflicting or incomplete evidence, and keeps task/experiment
  metadata bound to versioned manifest digests.
- A source-backed API and interactive dashboard recompute the selected
  population for category and split filters, keep full-set release gates
  separate, expose reproduction digests, and distinguish synthetic,
  development, and release evidence.
- Loading, unavailable, empty-selection, and no-failure states are explicit;
  synthetic preview values are visibly prohibited from use as performance
  claims.

## Local verification

On 27 September 2026:

- Benchmark reporting, export, and dashboard API contract tests: 13 passed.
- Complete Python suite: 101 tests, 92 passed, 9 environment-dependent tests
  skipped.
- Next.js type check and optimized production build: passed.
- Browser verification: full-population rendering, category filtering,
  impossible-filter empty state, full-set gate isolation, desktop layout, and
  mobile overflow containment passed.
- Diff whitespace validation: passed.

The tests use explicitly synthetic records and do not constitute a benchmark
performance claim.

## Exit gates still open

- Expand the private benchmark from 5 to 20 balanced, independently validated
  tasks, including at least 10 unseen holdout tasks.
- Execute the baseline and at least two controlled experiments.
- Reproduce the selected result from a tagged product revision and benchmark
  manifest.
- Publish the verified metrics only after every release-evidence gate passes.
