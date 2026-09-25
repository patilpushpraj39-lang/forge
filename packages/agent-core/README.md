# Forge Agent Core

This package owns Forge's provider-neutral run store and bounded model loop.
Provider response objects do not escape their adapters.

The loop provides:

- typed model-step requests and results;
- strict tool-call validation before execution;
- token, cost, wall-time, model-step, tool-call, and patch-attempt budgets;
- stable idempotency keys for paid model steps and side-effecting tools;
- in-memory, SQLite, and PostgreSQL idempotency ledgers;
- bounded retry rules and explicit stop reasons; and
- an OpenAI Responses API adapter behind `ModelRuntime`.

The OpenAI SDK is optional. Install `forge-agent-core[openai]` only for live
provider calls. Model pricing is supplied by configuration rather than embedded
in the package so recorded costs can be tied to a dated benchmark manifest.
