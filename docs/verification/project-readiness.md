# Forge development readiness checklist

As of 4 October 2026, Forge is ready for the
[bounded development demo](../operations/project-demo.md), with its normal
local services and approved reviewer access available. It is not yet a verified
live autonomous engineering service or a production deployment. Paid AI remains
disabled in this development checkpoint. This record does not authorize enabling
AI, changing hosting or publishing to GitHub.

## Working and demonstrated

| Area | Evidence available | Boundary |
| --- | --- | --- |
| GitHub source capture | Recorded local ingestion into an immutable snapshot; exact commit and snapshot metadata shown in the pilot. | Disposable repository and development profile, not controlled multi-user proof. |
| Saved-source worker | Completed README-only run with successful command, indexing and workspace cleanup, recorded in the Milestone 6 follow-up. | No AI-generated patch or evaluated change in that run. |
| Signed-in review receipt | Owner screenshots show the original request-review receipt restored after sign-in. | Record-only; no upload, spending, patch or GitHub-write authority. |
| Code and test workflow | Local check reproduced the known failure, then passed 3 public tests and 4 independent checks per run. | Predetermined public patch and local temporary workspaces, not model quality or untrusted-code isolation. |
| Fixture review integrity | Both scripted choices restore; stale evidence/conflicts are blocked and exact replay does not add an event. | Temporary SQLite records, not human approvals or live publication. |
| Access safeguards | 30 isolated policy checks passed without skips; local browser restriction and restore screenshots supplied. | Fixture sessions plus partial development-browser evidence, not deployed access proof. |
| CI checkpoint | Owner screenshot of CI #28 at `6a636ef` shows `verify` and `sandbox-abuse` successful. | Job-summary evidence, not independently inspected per-test logs or production infrastructure. |

The code-checkpoint local suite discovered 296 Python tests: 283 passed and
13 PostgreSQL/Docker checks were skipped, not passed. All 50 web tests passed.
CI's configured `verify` job includes PostgreSQL, the isolated access check,
the known-patch workflow, web tests, type checking and a production build.
Its separate Docker lane runs abuse and recovery checks. A green CI build or
container test is not proof that the current development API uses those
production boundaries.

## Still unverified or incomplete

- [ ] **Live AI quality:** use a real approved model on bounded tasks, with
  verified availability, billing, pricing and usage evidence. Fake responses
  and predetermined patches do not satisfy this gate.
- [ ] **Upload and spending consent:** separately authorize the exact source,
  request and allowance; validate exact token count and durable one-attempt
  protection. The existing record-only receipt cannot supply this consent.
  Historical price assumptions are not current quotes or billing guarantees.
- [ ] **Controlled deployment:** verify HTTPS, real Clerk sessions, PostgreSQL,
  digest-pinned Docker, persistent artifacts, proxy streaming, rate limits and
  recovery together under the controlled profile. CI coverage does not deploy
  or validate that integrated service.
- [ ] **Live access behavior:** check a real unapproved account, session expiry
  and account switching. The supplied signed-out screenshot covered the upper
  page; complete lower-page clearing and in-flight evidence removal were not
  independently inspected.
- [ ] **Multi-tenant isolation, if offered:** the current controlled allowlist
  gives approved reviewers shared tenant access. Per-user ownership or
  cross-tenant isolation is not established.
- [ ] **Live patch publishing:** separately approve the exact evaluated patch,
  validate permissions/base commit and verify one pull request plus recovery
  after an ambiguous result. Mock publisher tests and record-only choices do
  not prove live GitHub publication. No automatic merge or deployment is offered.
- [ ] **Model benchmark results:** collect uncontaminated live-model task
  outcomes, costs, latency and failure analysis before claiming solve rates or
  autonomous engineering performance.

## Next recommended action

Present the free demo and retain a redacted record of its terminal result and
the CI summary. Do not add another feature or repeat approvals just to obtain
more green checks. If services or evidence fail, diagnose that specific failure.

After the demo, choose a separately authorized next gate. A small live read-only
AI pilot requires billing readiness and fresh upload/spending consent; it still
would not prove the general engineering worker. A deployment validation requires
an agreed host and verified infrastructure. Neither is started by this checklist.

## Evidence references

- [Known-patch checkpoint and owner follow-up](code-workflow-checkpoint.md).
- [CI run 28 for 6a636ef](https://github.com/patilpushpraj39-lang/forge/actions/runs/37154522551),
  supplied as a screenshot; detailed job logs were not independently read for
  this consolidation.
- [GitHub source and worker follow-ups](milestone-6-progress.md).
- [Approved-user access evidence and limits](approved-user-access-checkpoint.md).
- [Record-only review contract](readonly-request-review.md).
- [Controlled API prerequisites](../operations/controlled-api.md).

This is an evidence summary, not a security certification, signed attestation
or launch approval. No credential files, live databases or saved receipts were
opened to prepare it. No servers, settings or application behavior were changed.
