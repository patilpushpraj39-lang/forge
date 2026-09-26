# Milestone 4 Bounded Autonomous Loop Verification

- Status: in progress
- Date: 2026-09-26

## Implemented evidence

| Check | Result |
|---|---|
| Objective and exact budgets persist through API/store/UI | Pass |
| Existing SQLite run schema upgrades with safe defaults | Pass |
| PostgreSQL objective/budget migration and constraints | Implemented; CI pending |
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
| Worker executes persisted objective and budgets | Pass with scripted runtime |
| Slow model calls renew the durable worker lease | Pass |
| Non-empty patch reaches `AWAITING_APPROVAL` | Pass |
| Disposable workspace is destroyed after patch capture | Pass |
| Generated runtime caches excluded from source diff | Pass |
| Exact TypeScript patch with blank context applies | Pass |
| Five private tasks reach approval automatically | 5/5 deterministic orchestration |
| Snapshot-plus-patch replay public tests | 5/5 pass |
| Source benchmark checkout remains unchanged | 5/5 pass |

The local suite collects 55 tests. Forty-seven pass. Five PostgreSQL integration
tests skip without a configured database and three Docker abuse tests skip
without a configured immutable test image.

The separate private benchmark harness ran all five seed tasks through the real
run store, worker, bounded loop, tool executor, sandbox, artifact store, approval
transition, clean snapshot replay, and public test command. Each used five
scripted model steps and four tool calls and reached `AWAITING_APPROVAL`.

This run deliberately consumed private answer patches. It proves the workflow
and caught two real sandbox bugs, but it does not prove that a language model can
discover any fix. `model_quality_claimed` is therefore false in the private
machine-readable report.

## Not yet claimed

- No live OpenAI request has been made from this checkpoint.
- The five private seed tasks have not yet been attempted by a live language
  model.
- No model-quality or task-success rate is reported.
- The new PostgreSQL idempotency test and Docker abuse lane still require a
  clean public GitHub Actions run.

## Next proof slice

1. Reproduce PostgreSQL migrations/idempotency and Docker abuse tests in CI.
2. Add independent patch verification against a clean replay sandbox.
3. Add a patch-bound approval action before any GitHub write.
4. Run the pinned live-model benchmark across all five tasks.
5. Publish success, cost, latency,
   stopping-reason, and diff metrics without exposing hidden tests or answers.
