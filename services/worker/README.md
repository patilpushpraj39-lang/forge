# Forge Worker

The worker advances durable run state through leased, versioned transitions.
It selects the local or Docker sandbox backend from explicit environment
configuration and records immutable snapshot artifact references in the event
timeline. Each step has a stable owner, bounded execution, and an explicit
failure reason.

`ForgeToolExecutor` is the model-facing boundary. It exposes only versioned,
strictly validated operations for repository manifests, search, bounded file
reads, dependency lookup, allowlisted commands, checksum-bound patches, and
content-addressed diffs. Package installation and arbitrary executable paths
are denied by the first command policy.

The worker has two explicit modes. The default fixed command preserves the
walking-skeleton smoke test. `--agent` executes the persisted objective with
the persisted run budgets and moves a non-empty patch to `AWAITING_APPROVAL`.
Provider calls and sandbox commands renew the worker lease; completed model and
tool operations use the configured SQLite or PostgreSQL idempotency ledger.

Live mode requires `OPENAI_API_KEY`, `FORGE_OPENAI_MODEL`, and dated price
inputs expressed as micro-US-dollars per one million tokens:

```text
FORGE_MODEL_INPUT_MICROUSD_PER_MILLION
FORGE_MODEL_CACHED_INPUT_MICROUSD_PER_MILLION
FORGE_MODEL_OUTPUT_MICROUSD_PER_MILLION
```

Prices are configuration rather than source constants so an evaluation report
can reproduce its original cost calculation. Run one queued task with:

```text
python -m forge_worker.main --agent --once
```
