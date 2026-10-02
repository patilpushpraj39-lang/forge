# Authenticated web transport

- Date: 2026-10-02
- Status: locally verified; fresh GitHub reproduction pending the next push
- Paid model calls, live Clerk requests and GitHub writes: none

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
the session changes. This behavior is implemented in the React provider; the
real Clerk account-switch/sign-out UI still needs live integration testing.

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

## Remaining gates

Live Clerk sign-in, real session expiry/account switching, authorized HTTPS CORS,
proxy streaming behavior and live GitHub App integration are not yet reproduced.
This change does not enable controlled deployment, multi-tenant authorization,
provider billing, or live benchmarks. Service policy consistency, TLS/rate limits,
durable storage/recovery and launch evidence remain separate gates.

References: [Clerk useAuth](https://clerk.com/docs/nextjs/reference/hooks/use-auth),
[Clerk token refresh](https://clerk.com/docs/guides/sessions/force-token-refresh),
and [SSE framing](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events).
