# Controlled API boundary

Keep `FORGE_API_MODE=development` for the current free, trusted local demo.
Do not expose that development API beyond loopback. No live infrastructure,
authentication account, provider billing, or deployment is enabled by this change.

`FORGE_API_MODE=controlled` is an opt-in, single-tenant backend guard for a later
deployment. It is not public-launch approval or a completed production profile.

## Startup prerequisites

Controlled startup checks configuration before opening a database or loading
GitHub credentials. It requires:

- `FORGE_AUTH_MODE=clerk`, with the existing public-key, authorized-party, and
  reviewer-allowlist configuration. Reviewer configuration is constructed before
  the database is opened.
- A PostgreSQL `FORGE_DATABASE_URL`, not the SQLite development fallback.
- `FORGE_SANDBOX_BACKEND=docker` and a digest-pinned `FORGE_SANDBOX_IMAGE`.
- An explicit valid `FORGE_SANDBOX_SCOPE`, unique per deployment sharing a daemon.
- An absolute `FORGE_ARTIFACT_PATH` for local artifacts, or the S3 backend with
  its explicit bucket. Operators must provide persistent storage themselves.
- One exact HTTPS `FORGE_WEB_ORIGIN` with no path, query, fragment, userinfo,
  wildcard, or trailing slash. `CLERK_AUTHORIZED_PARTIES` must contain only that
  exact origin; local development origins are not automatically allowed.

These are configuration checks, not runtime connectivity, storage durability,
image provenance, TLS certificate, or service-readiness proofs. `/health` is
liveness only. No migration, Docker command, provider request, or remote call is
performed by the profile validator itself.

## Request policy

Only exact `GET /health` (and `HEAD /health` if the router supports it) bypasses
reviewer authentication. All other HTTP paths, including event streams, run
history, patch review, cancellation, benchmarks, GitHub operations, and future
routes, require a valid authorized reviewer session. Missing/invalid sessions
return 401, non-allowlisted reviewers return 403, and known authentication
unavailability returns 503. Rejection messages do not echo verifier errors or
tokens. Unknown verifier failures also cannot execute a protected route.

The verified identity is reused within the request by approval/publication
dependencies. Clients cannot supply an actor field or URL token to choose it.
The existing exact-patch approval and matching-publication-actor rules remain.

Even an authorized reviewer cannot use `POST /runs` for arbitrary host paths or
any `/demo` route in controlled mode. Runs must use the existing server-side
GitHub ingestion path. Interactive API docs and the OpenAPI schema are absent.
WebSocket connections are denied; SSE remains the supported event transport.

Exact-origin CORS preflights are handled outside the authentication gate so
browsers can send bearer headers. A preflight never executes a protected route;
CORS is not authentication. TLS termination must be provided separately.

## Checks and remaining work

Run the standard local Python suite:

```powershell
.\.venv\Scripts\python.exe scripts/run-python-tests.py
```

It includes controlled startup rejection, protected route access, valid and
invalid locally signed sessions through the actual Clerk verifier, origin
policy, disabled local execution, disabled docs, SSE chunk preservation, and
WebSocket denial. These tests use fixture identities, not a live Clerk account.

Do not switch your current demo to controlled mode yet. The web console now
uses a shared bearer-authenticated transport for run restoration, review,
cancellation, GitHub operations, benchmark reads, and fetch-based SSE. Before
a future controlled deployment, build the web app with
`NEXT_PUBLIC_FORGE_API_MODE=controlled`, the exact HTTPS
`NEXT_PUBLIC_FORGE_API_URL`, and `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`; match the
backend profile and authorized web origin. These public values are build-time
configuration, not secrets. Never put an API key or Clerk secret in a public
environment variable. Keep both profiles in `development` for today's free demo.

The controlled console waits for a signed-in session before fetching evidence,
requires GitHub ingestion rather than local paths, and disables the offline
demo page. GET requests refresh a rejected session token once; mutations are
never automatically replayed. A final 401 or any 403 blocks the session scope,
clears rendered evidence, and asks the reviewer to sign out/re-sign in or switch
accounts. Signing out or changing the active session remounts consumers and
aborts their requests. Token strings stay in memory and headers, not URLs,
local storage or logs; protected requests do not use browser credential cookies
or caches and reject redirects.

The stream accepts only complete, validated, contiguous sequence events;
comments and split UTF-8/line endings are handled incrementally. Reconnects
request `after=<last accepted sequence>`, deduplicate replay, and fetch a token
again. Connections rotate every 45 seconds to reauthenticate; transient
failures use bounded backoff and stop after five consecutive unproductive
attempts. This is periodic reauthentication, not instant revocation of an
already-open connection. Proxy timeouts, buffering and live session changes
still require deployment-environment validation.

Run `pnpm web:test` for the deterministic transport regression suite. See the
[authenticated web verification record](../verification/authenticated-web.md)
for tested cases and the remaining live sign-in gate.

Before deployment, also verify live Clerk and GitHub App integration, align the
worker/publisher environment with this policy, protect database and artifact
access, configure TLS/proxy boundaries and rate limits, verify recovery and
backups, and complete the launch evidence gates. This profile supplies neither
multi-tenant authorization nor a full sandbox security proof. Paid model
benchmarking remains deferred until separately authorized.
