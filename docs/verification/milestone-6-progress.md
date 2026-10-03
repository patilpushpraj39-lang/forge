# Milestone 6 Progress: Human Approval and GitHub Publishing

Status: foundation complete locally; live integration gates remain.

## Implemented evidence

- Approvals bind the exact patch hash, verdict hash, repository, installation,
  base branch, base commit, action, actor, and expiry.
- Stale and expired approvals are rejected before a publication job exists.
- Publication jobs are durable, leased, idempotent, and recoverable.
- Temporary GitHub failures use a durable bounded retry delay.
- Cancellation is rejected after publishing starts.
- The review API returns only a checksum-verified patch artifact linked to the
  run's evaluation audit trail.
- The web interface displays the diff, checks, evidence hashes, explicit
  confirmation, pull-request metadata, and publication status.
- The publisher authenticates as a GitHub App, revalidates permissions and the
  base commit, and creates deterministic Git objects and a pull request.
- The API uses a server-side GitHub catalog to list active installations and
  accessible repositories without exposing installation credentials.
- GitHub run creation resolves the branch to an exact commit, rejects unsafe or
  truncated trees, verifies every blob, and persists a content-addressed source
  snapshot that the worker can use after the materialized checkout is gone.
- Retry tests recover an existing branch and a lost pull-request response while
  issuing only one pull-request creation request.
- Patch application is isolated from any parent Git checkout, including when a
  temporary sandbox is created inside another repository.
- Reviewer identity is derived from a verified Clerk session and a server-side
  allowlist; forged browser actor IDs are rejected.
- Publication requires the same authenticated actor that granted the approval.
- Authentication fails closed when Clerk is not configured.

## Local verification

On 27 September 2026:

- Python suite: 88 tests, 79 passed, 9 environment-dependent tests skipped.
- Next.js type check: passed.
- Next.js production build: passed.
- Browser inspection: passed for the create-run and GitHub-target entry screen.
- Diff whitespace validation: passed.

The skipped checks require configured PostgreSQL or Docker environments. They
are not counted as milestone completion evidence.

## Exit gates still open

- Controlled live Clerk sign-in and approval proof.
- Controlled live GitHub App proof of catalog discovery, immutable ingestion,
  one pull-request creation, and ambiguous-retry recovery without duplication.

## Local GitHub source-import pilot follow-up

On 3 October 2026, the owner supplied a development-console screenshot showing
the installed App and a selected private disposable test repository. Backend
logs showed successful installation and repository discovery, followed by a
503 for source import. This is local development evidence, not controlled-profile
or multi-user authorization proof.

A separately authorized, credential-redacted diagnostic reproduced the import
failure using the production catalog and transport. All upstream requests
succeeded, but a 60-byte README's Base64 response included two whitespace
characters. Strict decoding rejected this valid formatting with
`GitHub blob base64 is invalid`. The decoder now removes only ASCII space, tab,
CR and LF before strict Base64 decoding. Blob/tree metadata, decoded size and Git
object hash checks remain unchanged; arbitrary invalid characters and Unicode
whitespace still fail closed.

Verification after the fix:

- All ten catalog/decoder tests passed, including seven new regression tests.
- The full Python suite ran 182 tests: 170 passed and 12 environment-dependent
  PostgreSQL/Docker cases skipped. Skips are not passes.
- The same live, read-only catalog diagnostic verified and materialized the
  one-file, 60-byte source successfully in a disposable local directory, which
  was removed afterward. The diagnostic allowed only repository GETs and minting
  a temporary installation token with Contents/Metadata read permissions.
- No Forge run, paid inference, branch, commit or pull request was created by
  the diagnostic. The App private key and tokens were not printed or stored in
  tracked files. No user-owned server was stopped.

After restarting the API, the owner supplied a browser screenshot showing a
new run in `CREATED` with `run_created` and `github_source_ingested` events.
The events recorded the selected repository and `main` branch, exact commit
and tree IDs, one file totaling 60 bytes, and a snapshot artifact SHA-256 with
a 10,240-byte archive size. This demonstrates successful local API run creation
and recorded snapshot metadata; the saved archive bytes were not independently
re-read or hashed during this browser check.

The owner then cancelled this verification-only run. A second supplied
screenshot showed `CANCELLED`, `cancellation_requested`, and a direct
`CREATED` to `CANCELLED` transition, with the original ingestion event retained.
The owner reported only the API and web servers running, with no worker or
publisher. No live AI execution or pull-request publication was attempted.

These are user-supplied local development screenshots, not independently
inspected controlled-profile or multi-user proof. Controlled authentication,
human-approved live publishing and retry-recovery evidence remain outstanding.

## Targeted no-model worker check preparation

On 3 October 2026, a local-only helper was added for an explicitly selected fresh
GitHub-source run with a fixed README smoke-test objective. It validates the
expected repository and immutable source metadata before claiming that run,
restores the stored snapshot using artifact integrity checks, and executes only
an isolated Python README byte-count/hash command. It strips inherited
credentials and does not instantiate a model runtime or publisher. It refuses
cancelled or already-started runs and unsupported storage/profile settings.
See the [local worker smoke-check guide](../operations/github-worker-smoke.md).

Eleven new fixture-based tests passed, including preservation of an unrelated
queued run, immutable input use, missing/corrupt source failures, credential
removal/restoration, and CLI behavior. The full local Python suite ran 193 tests:
181 passed and 12 PostgreSQL/Docker environment-dependent cases skipped. These
skips are not passes. No actual pilot run was claimed or executed during this
preparation, and no paid inference or GitHub changes were made. Live worker
smoke-check evidence remains pending a new disposable run; this preparation
does not close controlled authentication, AI-quality or publication gates.

## Completed local worker smoke check

Later on 3 October 2026, the owner created a fresh disposable GitHub-source run
with the helper's exact task and executed the one-run check. The saved local API
run and audit trail were independently read afterward. They showed
`CREATED -> SNAPSHOTTING -> EXECUTING -> COMPLETED`, `snapshot_ready`, one
Markdown file indexed, and `command_completed` with exit code zero, a 60-byte
README count and SHA-256. The audit trail also recorded `workspace_destroyed`.
The run had no evaluated patch or evaluation verdict; no model/evaluation or
publication event was present. These checks did not re-execute the run.

The web console previously requested patch review for every completed run.
The local review API correctly returned 409 (`run has no evaluated patch`) for
this command-only run, producing a misleading red error. The console now skips
that request for completed runs with no patch/evaluation evidence and shows
`Completed without a patch. No patch review or publication is expected.`
Awaiting-approval and publishing runs still request review. Completed runs with
either hash or evaluation/approval history also request review, including SSE
updates before metadata refresh; incomplete evidence remains subject to the
existing API integrity checks. No API guard was relaxed and no 409 response is
blanket-suppressed.

All 44 web tests passed, including six review-selection regressions, and the
TypeScript check passed. The full Python suite was rerun: 193 tests, 181 passed
and 12 PostgreSQL/Docker environment-dependent skips (not passes). The existing completed-run page was reloaded and
independently inspected in the signed-in local browser: `COMPLETED`, the no-patch
explanation and the saved audit events were visible, with no 409 alert. A local
screenshot was saved outside tracked documentation. No live run was created or
re-executed during the display fix, no paid model calls were made, and no GitHub
changes or publication were performed. This is local worker/command proof, not
AI-quality, controlled-profile authentication or live publication proof.

## Development authentication follow-up

On 2-3 October 2026, local Clerk development sign-in displayed the verified
reviewer actor in a user-provided screenshot. The user subsequently reported
completing the sign-out/refresh/sign-in check, and an anonymous local request
to `/auth/me` returned 401. These checks do not close the controlled-profile
approval or GitHub publishing gates above. See the
[authenticated web verification record](authenticated-web.md) for provenance,
scope and remaining live checks. No paid model calls or GitHub writes were made.

## PostgreSQL CI gate reproduced

[CI #18 at `733ce1d`](https://github.com/patilpushpraj39-lang/forge/actions/runs/37109800643)
passed all eight real PostgreSQL integration cases, including the migration and
patch-bound approval/publication outbox cases. The public PostgreSQL gate is
therefore no longer listed as open. Live controlled-profile Clerk approval and
GitHub App publication remain separate, unfinished integration gates. See the
[free development checkpoint](free-development-checkpoint.md) for job counts
and the distinct Docker test lane.
