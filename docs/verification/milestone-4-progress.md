# Milestone 4 Bounded Autonomous Loop Verification

- Status: in progress
- Date: 2026-09-26

## Implemented evidence

| Check | Result |
|---|---|
| Provider-neutral request/result contract | Pass |
| Responses API payload translation | Pass with deterministic fake client |
| Strict serial function tools | Pass |
| Tool-output continuation preserves response and call IDs | Pass |
| Structured-output validation | Pass |
| Cached/reasoning token normalization | Pass |
| Configured cost calculation | Pass |
| Token, cost, time, step, tool, and patch budgets | Pass |
| Unknown/invalid tool denial before execution | Pass |
| Transient retry and permanent-error stop | Pass |
| In-memory duplicate-delivery protection | Pass |
| SQLite idempotency survives process-local ledger recreation | Pass |
| PostgreSQL idempotency integration test | Implemented; CI pending |
| Snapshot-bound manifest/search/file-read tools | Pass |
| Allowlisted command policy and denied installer command | Pass |
| Checksum-bound patch and content-addressed diff tools | Pass |

The local suite collects 45 tests. Thirty-seven pass. Five PostgreSQL integration
tests skip without a configured database and three Docker abuse tests skip
without a configured immutable test image.

## Not yet claimed

- No live OpenAI request has been made from this checkpoint.
- The bounded loop is not yet wired into the durable worker state machine.
- The five private seed tasks have not yet been attempted autonomously.
- No model-quality or task-success rate is reported.
- The new PostgreSQL idempotency test and Docker abuse lane still require a
  clean public GitHub Actions run.

## Next proof slice

1. Add objective and budget fields to the durable run contract.
2. Add agent, verification, and approval transitions and event payloads.
3. Execute the loop through the worker while preserving lease/cancellation
   behavior.
4. Run a deterministic offline workflow test across all five base fixtures.
5. Run the pinned live-model benchmark and publish success, cost, latency,
   stopping-reason, and diff metrics without exposing hidden tests or answers.
