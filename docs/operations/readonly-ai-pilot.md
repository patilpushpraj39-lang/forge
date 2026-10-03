# Read-only AI pilot: offline preparation

This preparation previews one small README-summary request. **It does not execute
an AI request or prove the end-to-end Forge AI worker.** Keep the existing web and
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
counts. It is tested offline, but **not wired into a live executor**. The CLI has
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
They are not yet connected to a paid provider adapter or Forge's general worker,
and do not close AI-quality, controlled-profile or publication gates. The real
paid pilot remains pending a supported payment method, funded API project,
separate consent and a reviewed retry-free provider integration. Do not change
payment details or start a general AI worker as part of this free check.

## Stop before execution

A future executor must be reviewed and tested before any upload or spending:

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
