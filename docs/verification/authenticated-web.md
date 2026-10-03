# Authenticated web transport

- Updated: 2026-10-03
- Status: local transport tests and development Clerk sign-in smoke verified;
  production integration gates remain
- The original implementation checks below made no paid model calls, live Clerk
  requests or GitHub writes. Later development Clerk checks are recorded separately.

## Implemented boundary

The run console and benchmark dashboard use one session-aware transport for
protected reads and commands. Controlled web mode waits for sign-in and fails
closed without Clerk configuration. Development without Clerk preserves the
trusted local flow; approval/publication/GitHub catalog still require a session.
The offline demo is disabled only in controlled web mode. No current local
profile or billing configuration has been changed.

Tokens are retrieved per request, attached to bearer headers, never persisted
or placed in URLs. GET rejection with 401 forces one Clerk cache bypass. POST
requests are not automatically replayed. Known access failures use safe local
messages rather than displaying provider response details. Cookies, caches and
automatic redirects are disabled on protected requests.

Clerk session identity keys the consumer subtree. Identity changes, sign-out,
or a final access rejection remount it, clear prior evidence/approval state,
and abort reads and streams. A rejected session scope remains blocked until
the session changes. Development sign-out/sign-in was manually checked as
recorded below; account switching and evidence clearing during an active run
still need live integration testing.

Native EventSource has been replaced with a fetch reader. UTF-8, comments,
multiline data and CR/LF/CRLF boundaries are parsed incrementally. Frames are
bounded to 1,048,576 decoded UTF-16 code units. Only complete, schema-checked, contiguous
sequence events advance the cursor. Duplicates are ignored; gaps, invalid
payloads and wrong content types stop without skipping evidence. Reconnects
send only the cursor in the query string, reacquire credentials, and stop on
access rejection. Transient failures use bounded backoff; active connections
rotate after 45 seconds for periodic reauthentication, not instant revocation.

## Local evidence

- 20 Node transport tests passed, without a live provider. Cases include all
  protected endpoint categories, no unauthenticated controlled requests, trusted
  development, bounded token refresh, no mutation retry, denial/sanitization,
  abort during token retrieval, URL/redirect policy, split UTF-8/SSE parsing,
  duplicate/incomplete replay, forbidden/malformed streams, bounded reconnects,
  reader cancellation, token rotation, sequence gaps and rejected-session blocking.
- TypeScript checks and the Next.js production build passed using existing
  installed binaries. The local pnpm wrapper attempted dependency reconciliation
  and refused a non-interactive module-directory replacement; no dependency
  replacement was performed. CI uses a fresh frozen-lockfile install.
- Python: 168 collected, 156 passed, 12 skipped (4 Docker and 8 PostgreSQL
  integration cases without local configuration). Scaffold and whitespace
  checks passed. The prior commit's real PostgreSQL/Docker reproduction is
  recorded separately in the controlled API verification record.
- Browser smoke: an isolated development API/store executed the deterministic
  offline demo, reached independent review, and restored that review on reload.
  The main console then restored the same run, loaded patch evidence and replayed
  all 14 events through the new fetch stream. Approval remained disabled without
  reviewer configuration. This is workflow evidence, not model-quality evidence.

The Node suite is included in GitHub CI via `pnpm web:test`. The suite runs
directly with Node's TypeScript stripping; it adds no dependency or external
network call. Fixture token strings are fake and not account credentials.

## Development Clerk smoke check

On 2-3 October 2026, using the local development profiles (not controlled mode):

- The user created a Clerk development account and completed sign-in.
- A user-provided screenshot showed the main console displaying the verified
  Clerk reviewer actor instead of the loading message. The backend had been
  configured with the app's public verification key, one reviewer subject and
  the exact authorized party `http://localhost:3000`.
- On 3 October, the user reported completing the requested sign-out, refresh
  and sign-in-again check. This is user-reported manual evidence, not automated
  browser verification or a proof of clearing evidence from an active run.
- The user also reported completing the signed-in offline-demo check: a fresh
  deterministic demo reached review, a record-only approve/reject decision was
  made, and the same decision was restored after refresh. This is manual
  workflow evidence, not live model quality or GitHub publication evidence.
- An independent local request to `/auth/me` without credentials returned 401
  after reviewer configuration was enabled. The earlier disabled backend had
  returned 503. Six deterministic reviewer-authentication tests also passed.
- Local environment configuration and the free backend launcher are ignored
  by Git. No real account ID, secret key or session token is included here.
- The local launcher removes inherited OpenAI credentials before starting the
  API. No paid model inference, benchmark execution or GitHub write was made
  during these checks. Clerk development network requests did occur.

The web server and browser both used `localhost:3000`. Binding the web server
to `127.0.0.1` while opening `localhost` previously caused a self-proxy connection
failure during the Clerk handshake; matching the host resolved the local smoke
check. The backend remains on loopback at port 8000.

Local startup worked after stopping the old API process and starting the
configured backend. During setup, a separate UI defect was observed: the
header kept saying "Checking reviewer authorization" after a failed request,
because the error was rendered only in the later review section.

## Reviewer-status regression follow-up

On 3 October 2026, the console was updated so its header, repository selector
and approval section share one explicit reviewer status. Backend failures are
visible before a run exists. Expired/forbidden sessions take precedence over
stale identities; unknown failures use a generic message without token/provider
details. A 15-second deadline bounds the complete reviewer check, including
token retrieval and response decoding. Unmounting cancels the check; timed-out
or cancelled checks cannot accept a late identity. No access policy was weakened.

- All 27 web tests passed: the original 20 transport cases and seven new
  reviewer-status/check cases, including 503 visibility, failure sanitization,
  expired/forbidden status, response validation, stalled requests, late results
  and cancellation while token retrieval ignores abort.
- TypeScript checking passed. No dependency was added or replaced.
- The new cases are included in the existing `pnpm web:test` CI command.
- Live browser validation of the new reviewer-failure display and CI
  reproduction remain pending; the user's earlier successful local sign-in
  and demo checks predate this presentation change.

## Optional GitHub catalog warning follow-up

On 3 October 2026, GitHub catalog failures were separated from engineering-run
errors. A catalog 503 now explains that the GitHub connection is not configured
or is temporarily unavailable, rather than declaring all of Forge unavailable.
The status alone cannot distinguish those two causes, so the UI does not claim
that every 503 is missing configuration. Unknown failures never echo arbitrary
provider details. No GitHub App configuration or access policy was changed.

Unchecking the optional GitHub selector hides and clears only its catalog warning,
aborts pending catalog requests, and stops catalog loading from disabling the
local form. Real run errors remain separate. GitHub submission requires a loaded
repository and no catalog error. Installation and repository loading are tracked
separately so one completed request cannot clear another request's loading state.

- All 30 web tests passed, including three new catalog-message/sanitization cases.
- TypeScript and whitespace checks passed; no dependencies were added.
- Browser verification against the running development services showed the scoped
  GitHub warning on selection, then no warning and an enabled local-run button on
  deselection. A rapid select/deselect check also ended without a stuck loading
  state. No run was submitted, paid model call made, or GitHub write performed.
- The existing successful reviewer identity was visible during this check.
  Reviewer-failure browser scenarios and CI remain unverified for these changes.
  The subsequent isolated production build and standalone smoke checks passed;
  see the [free development checkpoint](free-development-checkpoint.md) for their
  credential-free configuration and limits.

## Remaining gates

Controlled-profile Clerk sign-in/approval, real session expiry/account switching,
evidence clearing during an active run, authorized HTTPS CORS, proxy streaming
behavior and live GitHub App integration are not yet reproduced.
This change does not enable controlled deployment, multi-tenant authorization,
provider billing, or live benchmarks. Service policy consistency, TLS/rate limits,
durable storage/recovery and launch evidence remain separate gates.

References: [Clerk useAuth](https://clerk.com/docs/nextjs/reference/hooks/use-auth),
[Clerk token refresh](https://clerk.com/docs/guides/sessions/force-token-refresh),
and [SSE framing](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events).
