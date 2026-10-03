# Read-only AI pilot: preview, rehearsal and disabled executor

This preparation previews one small README-summary request. **The preview and
rehearsal CLIs do not execute an AI request or prove the end-to-end Forge AI worker.** Keep the existing web and
API terminals running and general workers/publishers stopped. Do not run
`forge_worker.main --agent --once`: that command can claim another queued run and
the general agent has editing/command tools.

## Preview locally (free, no upload)

Use the completed no-model README smoke check as an immutable **source reference**,
not a run to re-execute. In a separate terminal at the Forge project root:

```powershell
.\.venv\Scripts\python.exe scripts/prepare-readonly-pilot.py --run-id "COMPLETED-SMOKE-RUN-UUID" --repository "OWNER/DISPOSABLE-REPOSITORY"
```

The command reads the existing SQLite database in read-only mode, checks the
expected repository/commit, verifies the snapshot size/SHA-256, and reads its sole
regular `README.md` in memory. It never extracts an archive, creates a workspace,
claims/updates a run, reads environment/settings/API keys, executes repository
code, contacts a provider or writes to GitHub. Custom local storage paths can be
supplied with `--database` and `--artifacts`; both must already exist inside the
project. Only UTF-8 README text up to 4096 bytes and an archive up to 1 MiB qualify.
The displayed JSON includes the exact proposed request, content/commit hashes,
price assumptions, conditional estimate and a deterministic plan fingerprint.
Treat this output as private source data; do not publish it without review.

## Proposed request and spending gate

- Existing experiment model: `gpt-6-sol`, default service tier, reasoning `none`.
- README summary only, no tools, `store=false`, at most 512 generated tokens.
- Proposed execution policy: one generation attempt, zero automatic retries.
- Required exact input count: at most 2048 tokens. README bytes are **not** tokens.
- Conservative rates checked on 3 October 2026: $2.75 per million input and $11
  per million output tokens. These include cache-write/regional headroom over
  Standard short-context rates. No Fast, extended context or paid tools qualify.
- At those bounds, estimated token charges are at most **$0.011264**, below a
  proposed **$0.02 allowance**. This is conditional arithmetic, not a guaranteed
  total bill; prices, taxes and account settings can differ.

The pure `check_input_budget` validator rejects unknown/invalid or excessive
counts. It is tested offline and reused by the isolated, disabled-by-default
executor below; **no live execution is enabled or verified**. The preview CLI has
no execution option. Ordinary Forge worker budgets are not strengthened by this
helper and remain a separate hardening task.

## Free safeguard rehearsal (no card or API key)

In a separate terminal at the Forge project root:

```powershell
.\.venv\Scripts\python.exe scripts/rehearse-readonly-pilot.py
```

This command uses only a public, fixed README fixture and a built-in fake
response. It never reads the saved pilot run, reviewer settings or credentials,
and never constructs an SDK client. It has no live/execute/key/repository/run
option, and the rehearsal function rejects live/custom backends. It prints
`SIMULATED_COMPLETED`, zero actual provider calls/charges, and
`duplicate_attempt_blocked: true`. Token counts and response content are invented
fixtures, not a real tokenizer or AI result. The hypothetical cost is an offline
calculation, not observed provider spend.

The rehearsal checks the complete plan fingerprint, exact input content,
simulated confirmation, fixed no-tools request, output bound, allowance and
matching price-review date before the fake generation. The CLI deliberately
uses the frozen price date as its simulated review day; it does not verify live
prices. Altered tools, tiers, models, storage, retry settings or prices are
rejected even if the modified plan has a newly computed hash. The count gate
rejects oversized/invalid fake counts and excessive estimated charges. Counting
and generation receive separate copies of the identical approved request.

A separate SQLite rehearsal journal commits a unique attempt reservation before
counting and commits `SIMULATED_GENERATION_STARTED` before generation. Reopening
the journal cannot replay that attempt, including after timeout, interruption,
incomplete/malformed output or a lost result. Concurrent repeats also allow only
one attempt. The journal stores hashes/status/numeric usage, not README content,
response text or raw errors. The CLI removes its own temporary fixture/journal
on exit; reopening/recovery behavior is tested with persistent fixture journals.
Running the CLI again starts another **free simulation**, not a live retry.

These protections are implemented and tested only in this isolated rehearsal.
The rehearsal never connects to a provider adapter or Forge's general worker,
and does not close AI-quality, controlled-profile or publication gates. The real
paid pilot remains pending a supported payment method, funded API project,
separate consent and a reviewed retry-free provider integration. Do not change
payment details or start a general AI worker as part of this free check.

## Standalone read-only executor (disabled by default)

`scripts/execute-readonly-pilot.py` now contains a separate operator-only execution
function and direct HTTPS adapter. It is **not registered with the website, API,
general worker or publisher**. It cannot edit repository files, execute commands,
claim a run or publish a patch. Starting either Forge server does not invoke it.
The normal command is a safe status check only:

```powershell
.\.venv\Scripts\python.exe scripts/execute-readonly-pilot.py
```

It prints `DISABLED` without reading credentials, opening storage or sending any
request. There is no CLI enable/execute/key/reset option or environment-variable
enable switch. No live caller has been added. Do not enable the library function
as part of this free development stage, and never paste credentials into chat or
command-line arguments. Live counting/generation still needs separate consent.

The future trusted operator integration must explicitly supply an enable boolean,
project-scoped credential and project ID, the exact reviewed preview, and a
separate consent record. This record binds the preview hash, source run/repository/
commit, project, allowance, reviewed price day, explicit upload/spend consent,
billing/model verification and retention/estimate acknowledgements. Consent uses
UTC timestamps, expires within 15 minutes, and is rechecked after token counting.
Price assumptions must match the actual UTC day; the executor does not use the
rehearsal's simulated clock or silently refresh prices. Prices/rates remain frozen
in the preparation helper until a new verified review updates them. Confirmation
flags are operator attestations, not proof of account readiness or authenticated
user authorization. Future integration must authenticate approval server-side;
never construct this record from untrusted README/model output or a public client.

Before any upload, the executor reopens `.state/forge.db` in read-only mode and
rebuilds the plan from the verified captured archive. Changed source, commit, task,
run state, request, price assumptions or hashes stop execution. The old completed
smoke run is never updated or re-executed.

A separate fixed `.state/readonly-ai-pilot.sqlite3` ledger reserves **one total
attempt per local ledger**, not one per caller-chosen ID. Changing project/plan/run
does not create another slot. Reservation commits before counting, and the
generation-start transition commits before generation. Journal failure, invalid
count, insufficient allowance, expired consent, timeout, interrupted process,
incomplete/malformed response or lost result never causes automatic retry. There
is no reset/delete method. Preserve this ledger/backups: deleting/replacing it or
using another checkout/host can bypass this local guard. It is not an account-wide
budget service or a security boundary against a malicious local operator.

The direct standard-library HTTPS adapter uses only `api.openai.com` and two fixed
POST endpoints: `/v1/responses/input_tokens` then `/v1/responses`. It uses a
30-second socket timeout, verified HTTPS and no SDK, redirect following, proxy or
base-URL environment override, automatic retry, response polling or tool dispatch.
The count endpoint receives the same input/instructions/model/tools/reasoning as
generation, but omits generation-only parameters unsupported by the count schema.
HTTP/JSON responses are size-bounded. Errors are sanitized; no provider error body
is displayed. Generation uses the exact approved no-tools, default-tier,
`store=false`, reasoning-`none`, 512-output-token request. Counts are capped at
2048 and estimated maximum token charges must fit the confirmed allowance before
generation. Unknown/tool output, changed tier/model/storage, invalid usage or
control-character output is rejected without retry.

The ledger stores only hashes, fixed states and numeric counts/estimates. Valid
response text is returned in memory as **untrusted** data, never executed or
saved to a repository. Usage-derived charges are conservative estimates, not an
actual bill; `billing_verified` is always false. A malformed/lost outcome may
already have incurred charges even though no successful result is available.

Tests exercise this real executor/adapter path using a fake HTTP connection,
fixed public fixtures, fake keys/counts/outputs and temporary source/ledger DBs.
No actual provider connection, credential lookup, live Forge run update or GitHub
write is performed. This verifies local behavior, not live schema acceptance,
billing, model availability or AI quality. The older general worker is unchanged
and still requires separate request-level spending/retry hardening. See the
official [count endpoint schema](https://developers.openai.com/api/reference/python/resources/responses/subresources/input_tokens/methods/count)
and [response schema](https://developers.openai.com/api/reference/python/resources/responses/methods/create).

## Stop before execution

A future live integration must be reviewed and tested before any upload or spending:

1. Confirm the displayed README contains no secrets or sensitive/customer code.
2. Obtain explicit consent to send this exact content to OpenAI and pay for one
   generation, using the displayed model/rates/allowance.
3. Verify current pricing, model availability and the intended API project with
   billing enabled. Never paste keys into chat or commit them to Git.
4. Disable SDK and application retries; obtain the exact provider input count
   for the identical request and pass the budget gate **before generation**.
   Counting itself transmits source content and also needs consent.
5. Enforce a durable one-attempt guard before generation. A timeout/ambiguous
   response must not automatically cause another charged attempt.
6. Record provider usage, price assumptions and outcome without leaking keys.
   Do not mark this old smoke run as AI-tested or publish a patch.

No provider key, billing readiness, token count, paid response or AI quality was
verified by preparation. `store=false` is not a promise of zero provider retention.
An API project hard spending limit is defense in depth, not an exact instant cap;
alerts alone do not stop requests. See the official
[model/pricing documentation](https://developers.openai.com/api/docs/models/gpt-6-sol),
[token-counting guide](https://developers.openai.com/api/docs/guides/token-counting),
[spend-limit guide](https://developers.openai.com/api/docs/guides/spend-limits), and
[data controls](https://developers.openai.com/api/docs/guides/your-data).
