# Controlled API access verification

- Date: 2026-10-02
- Status: initial GitHub run failed on malformed PostgreSQL run IDs; fix locally verified, GitHub reproduction pending the next push
- Paid model calls, live Clerk requests, and GitHub writes: none

## Behavior

`FORGE_API_MODE` defaults to `development`, preserving the trusted loopback demo.
The opt-in `controlled` profile checks authentication mode, PostgreSQL,
digest-pinned Docker execution, explicit cleanup scope and artifact storage,
and a single exact HTTPS web/Clerk origin before opening dependencies.
Clerk reviewer configuration is constructed before the database is opened.
Invalid configuration errors identify settings, not their secret values.

Controlled mode uses a default-deny ASGI middleware around the entire HTTP
application. Only exact health liveness requests bypass reviewer verification.
Run reads, patch evidence, cancellation, benchmarks, publication, and SSE all
require a verified allowlisted reviewer. The identity is reused by existing
approval/publication dependencies; invalid identities fail closed. Known
authentication errors map to sanitized 401/403/503 responses. Unexpected
verifier exceptions do not execute the protected handler and return a generic
500 with application debugging disabled.

Authorized users still cannot execute local-path or offline-demo routes in this
mode. Interactive docs and schema routes are absent. CORS handles exact-origin
preflights outside the gate without running handlers. The gate preserves SSE
chunks and rejects WebSocket connections.

## Evidence

`tests/test_api_access.py` contains 16 tests covering:

- Development defaults and valid controlled/S3 configuration.
- Unsafe or missing configuration, malformed origins, and secret-safe errors.
- A subprocess import that fails before creating development state storage.
- Authentication on all representative data/command endpoints and SSE.
- The narrow public health exception.
- Disabled local-path/demo routes before any run-store write.
- One verified identity shared with the existing reviewer dependency.
- Locally RSA-signed sessions through the actual Clerk verifier: valid session,
  expired session, wrong origin, and non-allowlisted subject.
- Known verifier errors, invalid identities, and unexpected verifier failures.
- Exact-origin browser preflight handling and readable known rejection responses.
- Development compatibility, absent controlled docs, finite SSE delivery, and
  WebSocket denial.
- Malformed and missing run IDs: protected reads, review, event replay/SSE and
  cancellation require authentication first, then return 404; development demo
  reads also return 404.

The application-construction function used by these tests is also used by the
real API. Request-boundary tests attach the actual product handlers, use fixture
identities, and do not connect to live Clerk or GitHub services.

The initial local full suite collected 159 tests: 148 passed and 11 skipped
(4 Docker and 7 PostgreSQL tests without local configuration). The scaffold
and whitespace checks passed. Existing real-HTTP offline restart tests remain
part of this suite.

## PostgreSQL run-ID regression

[GitHub CI run 36995395150](https://github.com/patilpushpraj39-lang/forge/actions/runs/36995395150)
at commit `943d14a` failed the original development demo access test: a request
for `nonexistent` reached a PostgreSQL UUID query and raised
`InvalidTextRepresentation` instead of returning 404. The Docker abuse job
passed, but the verify job failed before web typecheck/build.

The adapter now rejects malformed and noncanonical IDs in run lookup before
UUID SQL. It does not resolve uppercase, compact or braced UUID aliases. Missing
event lists and targeted claims retain SQLite semantics (empty list/no claim).
The shared mutation lookup rejects invalid IDs before its query. Real database
errors for valid IDs are not reclassified as missing runs.

The original failing API test remains unchanged. Five database-free adapter
tests cover rejection before SQL, SQLite parity, valid missing UUID lookup, and
propagation of database failures. A new real-PostgreSQL integration test checks
that malformed reads, cancellation and claims leave an existing run and its
events unchanged.

After installing the existing PostgreSQL Python dependency into the local
virtual environment, the latest full suite collected 166 tests: 154 passed
and 12 skipped (4 Docker and 8 PostgreSQL integration tests without local
configuration). All five adapter tests executed locally. Scaffold and
whitespace checks passed. The new database-backed API/integration cases await
the next GitHub run; this record does not claim that run has passed.

## Remaining gates

This is a backend access-control slice, not a completed deployment. Authenticated
web reads and reconnecting SSE are still required: the current console has
unauthenticated fetches and a native EventSource. Other open gates include live
Clerk/GitHub integration, consistent worker/publisher policy, deployment TLS and
rate limits, storage/recovery/backups, and the product's launch evidence. The
guard supplies single-tenant reviewer access, not repository-scoped multi-tenant
authorization. Paid benchmarking remains deferred.

See the [operator guide](../operations/controlled-api.md).
