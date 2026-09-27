# Milestone 7 Progress: Benchmark and Hardening

Status: reporting, failure analysis, the balanced 20-task benchmark, and the
controlled experiment contract are complete locally; live executions and
tagged reproduction remain.

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
  zero-invalidation minimum.
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
- The separate private `forge-private-v1` manifest contains exactly 20 tasks,
  balanced at four each across frontend, backend, API, data, and test, with ten
  development tasks and ten holdouts.
- A strict private experiment contract selects the ten-task development split,
  pins the product and benchmark revisions, task and content digests, model,
  reasoning effort, service tier, prompt/tool versions, budgets, sandbox, and
  pricing snapshot, and refuses to freeze a dirty repository.
- The baseline and two one-variable experiments are matrix-checked. One changes
  only the tool-call ceiling; the other changes only the patch-attempt ceiling.
- A guarded executor verifies the frozen lock before constructing the runtime,
  requires an exact maximum-cost confirmation, injects hidden checks only into
  independent evaluation, and durably appends every completed task outcome.

## Local verification

On 27 September 2026:

- Benchmark reporting, export, and dashboard API contract tests: 13 passed.
- Complete Python suite: 103 tests, 94 passed, 9 environment-dependent tests
  skipped.
- Next.js type check and optimized production build: passed.
- Browser verification: full-population rendering, category filtering,
  impossible-filter empty state, full-set gate isolation, desktop layout, and
  mobile overflow containment passed.
- Private benchmark invariant suite: 20/20 base public pass, base hidden fail,
  patched public pass, and patched hidden pass.
- Independent-evaluation proof: 20/20 answer patches passed, 20/20 distractors
  failed, 20/20 unchanged submissions failed, 20/20 verdict replays were
  stable, and zero hidden tests appeared in solver snapshots.
- Deterministic orchestration proof: 20/20 tasks reached awaiting approval and
  replayed their public checks successfully. This uses stored answer patches
  and is not a model-quality result.
- Private benchmark checkpoint: `bb69833`.
- Experiment contract and execution-guard tests: 5 passed.
- The baseline, tool-budget, and single-patch experiment locks verified against
  their recorded product and private benchmark revisions.
- Diff whitespace validation: passed.

The dashboard tests use explicitly synthetic records. The private validation
proves task construction and evaluation behavior, not autonomous model
performance. Freezing the experiment inputs also makes no performance claim.
No paid model calls have been made and no benchmark performance claim has been
published. Baseline preflight is intentionally blocked until the optional
OpenAI SDK and an API key are configured.

## Exit gates still open

- Execute the frozen baseline and both controlled experiments.
- Reproduce the selected result from a tagged product revision and benchmark
  manifest.
- Publish the verified metrics only after every release-evidence gate passes.
