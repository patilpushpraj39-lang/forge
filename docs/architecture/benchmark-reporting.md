# Benchmark Reporting

Milestone 7 turns individual run outcomes into reproducible portfolio evidence.
The reporting boundary deliberately accepts versioned records rather than
reading mutable application tables: each released result can therefore be
reviewed, hashed, compared, and rebuilt from a saved JSONL artifact.

## Record boundary

One record represents one task under one experiment configuration and contains:

- benchmark, task, split, experiment, and task-version identifiers;
- the exact 40-character product revision and configuration digest;
- the evaluation-manifest digest and durable run identifier;
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

## Usage

```powershell
python scripts/build-benchmark-report.py results.jsonl `
  --json-output benchmark-summary.json `
  --markdown-output benchmark-card.md
```

The first real card will be generated only from recorded Forge runs. Synthetic
fixtures are used in tests, never presented as product performance.
