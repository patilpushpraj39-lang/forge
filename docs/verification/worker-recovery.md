# Worker lease-loss recovery

- Date: 2026-10-02
- Status: verified in CI for commit `c4801f6`
- Provider/model calls: none

## Behavior

A worker renews its claim before creating a workspace. If a different worker
already recovered the run, the stale worker records `worker_lease_lost` with its
worker ID and original attempt number and returns without starting work.

An ownership error during execution stops the bounded command and destroys the
old worker's workspace. The old worker does not mark the replacement attempt
failed. It also handles takeover racing its infrastructure-failure transition.
Ordinary infrastructure errors still fail the owned run and propagate to the
caller.

Fast smoke commands recheck ownership before recording their result. SQLite and
PostgreSQL validate transition ownership before checking the expected state, so
a stale worker is consistently classified as losing its lease even when
recovery also changed the state.

## Evidence

`tests/test_worker_recovery.py` exercises five local cases:

1. A stale claim creates no workspace.
2. Takeover during a real long-running Python command stops and reaps that
   command, closes its output pipe, and destroys its workspace. The replacement
   completes attempt 2 in a different workspace.
3. A genuine controller error still fails the run and cleans up.
4. Takeover after a fast command suppresses its stale completion result.
5. Takeover racing failure persistence does not fail the replacement attempt.

`tests/test_postgres_integration.py` includes a dedicated database check that
stale transitions raise `LeaseOwnershipError`, while an owned transition with
the wrong expected state still raises `InvalidTransitionError`.

The local suite ran 124 tests: 114 passed and 10 skipped (3 Docker and 7
PostgreSQL). The prior commit `9448537` passed
[CI run 10](https://github.com/patilpushpraj39-lang/forge/actions/runs/36977620664),
including the Docker abuse gate. The worker recovery changes subsequently passed
[CI run 11](https://github.com/patilpushpraj39-lang/forge/actions/runs/36978751324)
at commit `c4801f6`, including the PostgreSQL check. The later orphan-container
cleanup slice has [its own verification record](orphan-cleanup.md) and CI gate.

## Limits and follow-up

These tests force lease expiry in a disposable database and exercise a live
old command. They do not simulate a killed worker service or a host crash.
Orphan-container cleanup after abrupt process death has a separate pending proof.

Workers must have distinct IDs. This slice does not add attempt-number fencing
to every artifact/event write, prove exactly-once external execution, or resume
an interrupted provider call. It does not establish live-model task success.
