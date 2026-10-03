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
