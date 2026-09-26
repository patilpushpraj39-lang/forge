# Approval and GitHub Publishing

Milestone 6 separates human authorization from the component that can write to
GitHub. The API can record approval and enqueue work, but only the publisher
holds GitHub App credentials.

## Safety invariant

An approval authorizes one action against one immutable evidence tuple:

```text
repository owner/name
+ GitHub App installation
+ base branch and exact base commit
+ evaluated patch hash
+ evaluation verdict hash
+ create-pull-request action
+ reviewer identity
+ expiry
```

Changing any member of the tuple makes the approval unusable. Request and
publication idempotency keys are stored with their complete request shape; a
key reused for different input is rejected.

## State and data flow

```text
EVALUATING
  -> AWAITING_APPROVAL
  -> approval recorded (run state unchanged)
  -> publication job committed + PUBLISHING
  -> publisher claims a lease
  -> snapshot and exact patch replayed
  -> GitHub permissions and base SHA revalidated
  -> deterministic commit and branch created
  -> existing-or-new pull request resolved
  -> COMPLETED
```

The publisher reconstructs changed file bytes from stored artifacts. It does
not trust browser-submitted diff text. The GitHub branch name, commit content,
commit metadata, and head lookup are deterministic, so a retry can recover a
lost response without creating a second branch or pull request.

Transient failures return the same durable job to `PENDING` with exponential
delay. Permanent evidence, permission, or stale-base failures end the run. A
claimed job can be recovered after its lease expires. Cancellation is rejected
once a run enters `PUBLISHING` because the external write may already have
occurred.

## Review boundary

`GET /runs/{run_id}/review` returns only evidence linked from that run's audit
events. Before returning a diff, the API verifies the content-addressed
artifact and requires its checksum to equal the evaluated patch hash. The UI
shows the patch hash, verdict hash, base commit, changed files, verification
checks, and exact diff before enabling the approval action.

## GitHub authority

The production publisher signs a short-lived GitHub App JWT and requests an
installation token limited to metadata read, contents write, and pull-request
write. The token is kept only in memory and refreshed before expiry. Immediately
before creating the pull request, the publisher rechecks that the selected base
branch still points to the approved base commit.

## Remaining production gates

- Replace the local reviewer claim with authenticated identity and authorization.
- Resolve installations and repositories through a server-side GitHub catalog
  rather than manual IDs.
- Ingest the source snapshot directly from the selected immutable GitHub commit.
- Execute the PostgreSQL migration and publication contracts in public CI.
- Run a controlled live GitHub App test that creates exactly one pull request,
  including an injected ambiguous-response retry.
