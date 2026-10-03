# Signed-in record-only request review

Open `/readonly-pilot`. Sign in with an allowlisted Clerk reviewer, enter the
completed fixed README smoke-test run UUID and its expected `owner/repository`,
then load the preview. A URL can supply `?run=<UUID>&repository=<owner/name>`;
loading remains an explicit authenticated action.

The page shows the captured README as plain text, the exact proposed request,
repository/commit/source/request hashes, dated price assumptions, conditional
estimate, proposed allowance, and outstanding checks. It does not contact an AI
provider, count tokens, read an AI key, or create a workspace. Bytes are not a
token count, and the dated estimate is not verified current billing evidence.

After reviewing, acknowledge the record-only scope and record approval or
rejection. The authenticated API rebuilds the request from the immutable source;
the browser cannot supply source bytes, prices, an actor, or execution flags.
The event records the verified reviewer, timestamp, decision, source/commit and
request fingerprint. The opaque submission key is stored only as a hash. The
source run state and run row remain unchanged; no patch approval or publication
job is created. PostgreSQL uses the existing run-event audit outbox only, not a
publication command.

SQLite uses `BEGIN IMMEDIATE`; PostgreSQL locks the source run row. Identical
replays by the same reviewer return the original receipt. A changed decision,
reviewer, or submission key for an already-decided fingerprint is rejected.
Reloading restores the receipt. An uncertain POST outcome disables decisions
until reload; no automatic mutation retry is performed.

Both preview and decision require verified reviewer authentication, even under
the development API profile. Responses are private/non-cacheable. Changed
sessions clear browser evidence and abort in-flight requests through Forge's
existing session provider. README text is never interpreted as Markdown/HTML.
Review source instructions remain untrusted data, never application authority.

## Explicit non-authority

Scope: `readonly-preview-record-only-v1`. Every receipt explicitly sets
execution, upload, paid generation, file-change and GitHub-write authority to
false. This record is not the standalone executor's
`readonly-readme-openai-v1` consent. No API enable or execution route exists.
The executor remains disabled by default. Live use requires a separate future
operator workflow with explicit upload/spending consent, fresh pricing/billing,
model/key checks and the exact-count/one-attempt safeguards.

This feature proves authenticated, fingerprint-bound review recording only.
It does not prove AI quality, billing, deployment readiness, or end-to-end
autonomous engineering.

## Verification

- API fixtures: signed-in-only access; no anonymous/expired/unapproved bypass;
  canonical CLI fingerprint parity; unchanged source; receipt restore/replay;
  stale/conflicting decisions; strict non-execution schema; artifact integrity;
  concurrent atomic recording; source-change race rejection.
- Browser helpers: bounded preview parsing, source matching, non-authority flags,
  record-only request bodies, receipt validation and restore.
- Existing executor/preparation/rehearsal tests remain unchanged in meaning.

Restart only the backend after updating Python code (the local reviewer launcher
does not auto-reload). Keep the website running. Neither generic workers nor a
publisher nor the standalone AI executor is needed for this review screen.

Local checkpoint verification on 2026-10-03: 268 Python tests discovered,
255 passed and 13 infrastructure-dependent checks skipped; 50 web tests passed;
TypeScript and scaffold validation passed. An isolated credential-free Next.js
production build passed without changing the running website's build output.
The signed-in form was observed in the browser. Real-source preview/decision
verification awaits the user restarting the existing backend; no real-user
approval was recorded during development. PostgreSQL concurrency coverage is
included for CI but was not executed locally without a dedicated test database.
