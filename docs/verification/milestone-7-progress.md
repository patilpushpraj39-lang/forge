# Milestone 7 Progress: Benchmark and Hardening

Status: reporting foundation complete locally; task expansion and experiments
remain.

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

## Local verification

On 27 September 2026:

- Benchmark-reporting contract tests: 5 passed.
- Complete Python suite: 93 tests, 84 passed, 9 environment-dependent tests
  skipped.
- Diff whitespace validation: passed.

The tests use explicitly synthetic records and do not constitute a benchmark
performance claim.

## Exit gates still open

- Expand the private benchmark from 5 to 20 balanced, independently validated
  tasks, including at least 10 unseen holdout tasks.
- Execute the baseline and at least two controlled experiments.
- Connect completed run evidence to the strict result-record export.
- Build the failure-analysis dashboard.
- Reproduce the selected result from a tagged product revision and benchmark
  manifest.
- Publish the verified metrics only after every release-evidence gate passes.
