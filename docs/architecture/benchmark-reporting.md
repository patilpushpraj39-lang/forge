# Benchmark Reporting

Milestone 7 turns individual run outcomes into reproducible portfolio evidence.
The reporting boundary deliberately accepts versioned records rather than
reading mutable application tables: each released result can therefore be
reviewed, hashed, compared, and rebuilt from a saved JSONL artifact.

## Record boundary

One record represents one task under one experiment configuration and contains:

- benchmark, task, split, experiment, and task-version identifiers;
- the exact 40-character product revision and configuration digest;
- the immutable task-manifest digest, optional evaluation-manifest digest, and
  durable run identifier;
- a typed verdict and failure code;
- regression status, patch attempts, duration, cost, and tool calls; and
- category and language labels used for composition checks.

The [machine-readable schema](../../packages/contracts/benchmark-record.schema.json)
and parser reject missing or unknown fields, mixed product/configuration
revisions, duplicate task IDs, unpinned revisions, malformed digests, and
internally inconsistent verdicts. A solved task must be regression-free and
cannot carry a failure code. Every other verdict must explain its failure.

## Denominators

Forge reports invalidation instead of silently deleting inconvenient results:

- `records` includes every supplied record;
- `valid` excludes only `task_invalid` records;
- `scored` also excludes `infra_invalid` records; and
- solve, first-attempt, and regression-free rates use `scored` as their
  denominator.

Infrastructure failure rate uses valid attempted tasks as its denominator.
Cost and latency distributions include infrastructure-invalid attempts because
they consumed real resources. Cost per verified solve includes all cost from
valid attempts, so failures cannot make successful runs look artificially
cheap.

## Evidence generated

The reporter produces canonical JSON and a human-readable benchmark card with:

- verified solve rate and a 95% Wilson interval;
- first-attempt and regression-free rates;
- infrastructure-failure rate;
- median and nearest-rank p95 duration, cost, and tool calls;
- average cost per attempted task and verified solve;
- category, language, split, and failure distributions; and
- task-set, record-set, and summary digests.

Input ordering cannot change the summary or its digest. Release-evidence gates
require at least 20 valid tasks, all five planned categories with counts
differing by no more than one, at least ten
previously unseen holdout tasks, no invalid tasks, no infrastructure-invalid
results, and at least one scored result. These gates validate one result set;
they do not replace the separate requirements for controlled baselines,
experiments, a tagged revision, and independent reproduction.

## Durable run export

Benchmark rows are not entered by hand. The exporter reads the run and its
ordered event stream from Forge's configured durable store. It derives the
verdict, primary failure, public-regression status, run-to-verdict duration,
model cost, tool calls, patch attempts, and evaluation-manifest digest. Task and
experiment metadata come from versioned manifests supplied to the command.

The export fails when the run has no verdict, represents a smoke command rather
than an agent attempt, lacks required telemetry, contains out-of-order events,
uses naive timestamps, or has evaluation events that disagree about their
manifest. Pre-evaluation failures remain reportable with a null evaluation
manifest while retaining the required task-manifest digest.

## Failure-analysis dashboard

The `/benchmarks` product route reads the same strict JSONL records through the
API rather than maintaining a second analytics model. `GET /benchmarks/latest`
revalidates the configured artifact, recomputes its canonical summary, and can
apply category and split filters. Every headline card, comparison, failure bar,
and task row uses that one selected population. Full-set release gates remain
visibly separate and never change when a diagnostic filter is active.

The source must be classified as `synthetic`, `development`, or `release`.
Synthetic and development views carry an explicit non-release warning. A
release classification is not presented as ready unless every encoded
release-evidence gate passes. The interface also preserves unavailable, empty,
and loading states, shows the Wilson interval instead of a point estimate
alone, and exposes the benchmark, experiment, code, configuration, selected
summary, and full-evidence digests needed to reproduce what is on screen.

The checked-in dashboard fixture is deliberately synthetic and exists only for
tests and visual verification. It is not Forge performance evidence.

## Usage

```powershell
python scripts/export-benchmark-record.py `
  --run-id <run-id> `
  --benchmark-version forge-private-v1 `
  --task-id backend-fix-001 `
  --task-version task-v1 `
  --task-manifest-digest <sha256> `
  --split holdout `
  --category backend `
  --language python `
  --experiment-id tool-loop-v1 `
  --configuration-digest <sha256> `
  --code-revision <full-git-sha> `
  --output result.jsonl

python scripts/build-benchmark-report.py results.jsonl `
  --json-output benchmark-summary.json `
  --markdown-output benchmark-card.md
```

The first real card will be generated only from recorded Forge runs. Synthetic
fixtures are used in tests, never presented as product performance.
