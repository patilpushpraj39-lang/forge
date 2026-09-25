# Bounded Agent Loop

## Purpose

Forge owns the orchestration loop instead of hiding it inside a provider SDK.
This keeps budgets, retries, tool policy, idempotency, sandbox access, and stop
conditions testable as ordinary application code.

## Boundary

The workflow depends on a small `ModelRuntime` protocol. A request contains the
objective, immutable context references, allowed versioned tools, a strict
output schema, remaining budgets, prompt/tool versions, and an idempotency key.
A result contains normalized usage, cost, tool calls, structured output, trace
references, and a provider-neutral stop reason. Provider response objects never
cross this boundary.

The first adapter uses the OpenAI Responses API. Its request shape follows the
documented function-calling loop: function calls are returned to Forge, Forge
executes them under policy, and matching `call_id` outputs continue the prior
response. Tools use strict JSON schemas and parallel tool calls are disabled so
budget charging and side effects remain ordered.

## Execution flow

```text
durable run + immutable context
            |
            v
     budget pre-check
            |
            v
  idempotent model step ---- transient failure ---> bounded retry
            |
            v
 validate every tool call
            |
            v
 idempotent sandbox tool
            |
            +----> repository read/search with provenance
            +----> allowlisted bounded command
            +----> checksum-bound patch and diff artifact
            |
            v
   next model step or explicit stop
```

## Budget and stop semantics

The loop enforces token, configured cost, wall-clock, model-step, tool-call,
and patch-attempt limits before another paid step or side effect. It also stops
for cancellation, policy denial, invalid provider output, exhausted retries,
normal completion, or approval. A model cannot increase these limits through a
tool argument.

Cost uses a model-pricing value supplied by the caller. This avoids silently
changing historical benchmark math when provider prices change.

## Idempotency

Model-step keys include the run, logical step, previous response, versioned
prompt/tools, and pending tool outputs. Tool keys include the run, call ID,
tool name, arguments, and tool version. SQLite supports local durable replay;
PostgreSQL is the production source of truth. Re-delivery reads the recorded
result instead of repeating a paid request or completed side effect.

## Tool policy

Every tool has a strict object schema: all properties are required and extra
properties are rejected. Unknown tools and invalid arguments stop the loop
before execution. The initial command policy allows only Node/npm/pnpm and
Python entry points, denies package-install/publish/exec subcommands, limits
arguments, time, and output, and still relies on the sandbox controller as the
security boundary.

Repository reads are bound to the snapshot hash and return path/line
provenance. Patch application accepts UTF-8 unified diffs only, validates path
containment, and can require an exact SHA-256 digest.

## Remaining integration

The loop is not yet the default worker path. The durable task contract must add
an objective, budget configuration, agent/verification/approval states, and
event payloads. Then Forge will run the five private seed tasks with a pinned
model, prompt, tool version, sandbox image, and pricing manifest. That run—not
unit coverage alone—is the Milestone 4 exit evidence.

## References

- [OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling)
- [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [OpenAI Responses migration guide](https://developers.openai.com/api/docs/guides/migrate-to-responses)
