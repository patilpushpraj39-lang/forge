# ADR 0004 Provider Neutral Model Runtime

- Status: accepted
- Date: 2026-09-25

## Context

Model APIs, tool surfaces, and managed orchestration options change. Benchmark comparisons and reproducible experiments require provider details to remain outside the workflow domain.

## Decision

Forge will define typed model-step inputs and outputs. The initial direct-control adapter uses the OpenAI Responses API. Provider response objects, conversation identifiers, and usage formats are translated at the adapter boundary.

## Alternatives

- Call one provider directly throughout the codebase.
- Start with the managed Agents API as the only runtime.
- Build a broad provider abstraction before the first working loop.

## Consequences

The initial interface remains deliberately small: bounded step request, allowed tools, structured output schema, usage, stop reason, and trace references. New adapters are added only after the first path works. A later experiment can compare custom orchestration with the managed Agents API on the same benchmark subset.

