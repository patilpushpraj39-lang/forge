# Approved-user access checkpoint

Date: 2026-10-04. Scope: isolated controlled request-policy fixtures and a manual
development-browser checklist, not deployment or live AI execution.

## Starting evidence

The owner supplied a screenshot of CI #26 at `9ee9cde` showing both `verify` and
`sandbox-abuse` successful. After restarting the local backend and loading the
read-only review page, the owner supplied screenshots showing the same captured
source/request fingerprints and the original receipt ID/timestamp restored.
The UI states "Approval recorded — not executed" and that the source run is
unchanged. These are owner-provided CI/browser results, not a new approval or a
paid execution result. No real reviewer identity or credentials are copied here.

## Added verification

`scripts/check-controlled-access.py` runs 30 required checks in an isolated
Python child with temporary storage and inherited service/credential settings
removed. Connection initiation and DNS lookups are blocked before product
imports. Windows' exact standard-library private loopback socketpair creation
is permitted so in-process ASGI tests can run; ordinary localhost connections
are blocked. This is a test isolation helper, not a production security boundary.

Eight new controlled read-only tests use fresh RSA signing keys, fixture Clerk
subjects, the actual Clerk SDK signature/origin/expiry verifier, controlled
middleware and the real preview/decision handlers. A temporary SQLite store and
in-memory captured README supply test evidence; controlled startup prerequisites
are validated from an explicit fixture mapping, without connecting to its example
PostgreSQL host or Docker image. The full controlled stack's infrastructure is
therefore not reproduced by these tests.

The new checks cover approved reads, denied reads/decisions before source access,
receipt restore/replay, trusted identity recording, forged identity/spending
fields, another approved reviewer attempting to overwrite a decision, verifier
unavailability and exact-origin preflight behavior. Existing controlled access
and reviewer tests also run. Seven runner tests cover environment isolation,
socketpair compatibility, connection/DNS/datagram blocking, child working-directory
cleanup, error/timeout handling and refusal to count missing/skipped groups as a
passing checkpoint.

CI runs the repeatable checkpoint in addition to the full Python suite. No API,
web UI, worker, publisher, deployment profile or paid-execution behavior changed.
The shared temporary read-only test setup was extracted into a fixture base;
the original development API tests remain included without inherited duplicates.

Local verification on 2026-10-04: all 30 isolated access checks passed with no
skips. The full Python suite discovered 286 tests: 273 passed and 13
PostgreSQL/Docker infrastructure checks skipped (not passes). All 50 web tests,
scaffold validation and whitespace checks passed. The running website's build
output and existing generated `next-env.d.ts` change were left untouched. The
owner subsequently supplied a successful CI #27 screenshot at `2cc119f`; the
manual browser follow-up and its limits are recorded below.

## Remaining manual/infrastructure evidence

- Follow the [manual sign-out/restore checklist](../operations/approved-user-access-checkpoint.md).
  Owner-provided follow-up screenshots showed the signed-out read-only review
  restriction and then the original receipt/timestamp restored after sign-in.
  The screenshot showed the signed-out page's upper portion, not every page area;
  clearing during an in-flight request and the complete lower-page state were
  not independently inspected. This is local development browser evidence only.
- A real unapproved account, real session expiry and account switching remain
  unverified; local fixture subjects are not live Clerk accounts.
- Real controlled-profile HTTPS, Clerk, PostgreSQL, Docker, persistent artifacts,
  proxy streaming, rate limits, recovery and service-policy consistency remain
  deployment gates. Current development profiles are intentionally unchanged.
- This single-tenant allowlist gives approved reviewers shared read access.
  It does not prove per-user ownership or multi-tenant isolation.
- Paid generation, live AI quality, spending consent and GitHub publication are
  not enabled or proven by a record-only approval or these access checks.

No real credentials, saved source runs or receipts were accessed by the fixture
checkpoint. No provider calls, live database writes, repository-target edits or
GitHub writes were made. Current server processes were not stopped or restarted.
