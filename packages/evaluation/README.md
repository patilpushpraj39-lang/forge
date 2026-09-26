# Forge Evaluation

This package independently evaluates a stored patch against its immutable base
snapshot. It is not imported by the model-facing tool layer.

The evaluator:

- rejects empty, oversized, forbidden, or test-modifying patches before
  execution;
- reconstructs a clean sandbox from the snapshot and applies the exact patch
  hash;
- runs trusted regression, lint, type, build, and public-test commands;
- injects hidden acceptance tests only after the solver has stopped;
- records a typed failure taxonomy and a separate deterministic rubric; and
- produces both a full content-addressed report and a stable verdict hash.

The stable verdict hash excludes timestamps, durations, and raw command output.
Re-evaluating the same versioned inputs therefore yields the same verdict even
when incidental logs differ.

The package also owns the benchmark evidence contract. Its strict result record
and deterministic summarizer separate solver failures, infrastructure
invalidations, and invalid tasks; compute solve-rate uncertainty, latency,
cost, regression, and failure metrics; and produce content-addressed JSON and
Markdown benchmark cards. A separate exporter derives each record from the
durable run/event audit trail rather than accepting hand-entered outcome
metrics. See
[`benchmark-reporting.md`](../../docs/architecture/benchmark-reporting.md).
