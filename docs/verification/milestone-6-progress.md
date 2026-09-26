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

## Local verification

On 26 September 2026:

- Python suite: 82 tests, 73 passed, 9 environment-dependent tests skipped.
- Next.js type check: passed.
- Next.js production build: passed.
- Browser inspection: passed for the create-run and GitHub-target entry screen.
- Diff whitespace validation: passed.

The skipped checks require configured PostgreSQL or Docker environments. They
are not counted as milestone completion evidence.

## Exit gates still open

- Authenticated reviewer identity and authorization.
- Public CI execution of the PostgreSQL approval/publication migration.
- Controlled live GitHub App proof of catalog discovery, immutable ingestion,
  one pull-request creation, and ambiguous-retry recovery without duplication.
