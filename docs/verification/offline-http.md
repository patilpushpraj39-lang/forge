# Offline HTTP restart and integrity verification

- Date: 2026-10-02
- Status: local verification and GitHub CI passed at commit `6319ba1`
- Paid provider calls and GitHub writes: none

## New evidence

`tests/test_offline_demo_http.py` starts a real Uvicorn API on a reserved,
OS-selected loopback port. It uses temporary SQLite and content-addressed
artifact storage, strips inherited integration configuration, and executes the
fixed public offline fixture through the existing worker and evaluator.

Three tests prove:

1. Both approved and rejected record-only decisions survive complete API process
   shutdown and restart. Patch evidence is unchanged, retries return the original
   decision, a conflicting decision is rejected, and exactly one decision event
   exists. Publication is blocked by disabled reviewer authentication and no
   approval or publication event appears.
2. The actual HTTP boundary rejects stale patch/verdict hashes (409), unsupported
   decisions, short idempotency keys, and caller-selected actor fields (422).
   These rejected requests leave the durable event trail unchanged. Unknown demo
   IDs return 404.
3. A persisted patch modified without changing its size fails digest validation
   after server restart. Missing patches also block review. Neither case can
   record a decision; restoring the original test artifact restores review.

The harness uses a random-token shutdown endpoint attached only to the test
application process, not to the Forge product API. It requests graceful Uvicorn
shutdown before waiting for process exit and cleaning disposable data. This
avoids terminating only a Windows virtual-environment launcher. A bounded forced
cleanup fallback targets only its own still-owned subprocess tree.

## Local checks

- Isolated HTTP suite: 3 passed.
- Full Python suite: 144 collected, 133 passed, 11 skipped (4 Docker and 7
  PostgreSQL checks without local configuration).
- Scaffold validation and whitespace validation: passed.

An initial full-suite attempt exposed a Windows directory-in-use error during
test cleanup. After changing the harness to graceful server shutdown, the full
suite passed. No product authentication or publication protections were weakened.

The standard CI Python discovery includes these tests without a separate opt-in.
Their Linux/GitHub reproduction passed in
[CI run 13](https://github.com/patilpushpraj39-lang/forge/actions/runs/36993792691)
at commit `6319ba1`, with both `verify` and `sandbox-abuse` successful. The earlier
Docker crash-cleanup change already passed
[CI run 12](https://github.com/patilpushpraj39-lang/forge/actions/runs/36991989591)
at commit `1e8d038`; that result does not include these new HTTP tests.

## Limits

This is real HTTP and SQLite process-restart evidence, not browser automation,
deployment evidence, PostgreSQL demo-restart evidence, simultaneous-review
linearizability, live Clerk/GitHub integration, or autonomous model-quality
evidence. The local sandbox executes only the trusted bundled fixture and is
not a security sandbox. Paid benchmarking remains deferred.

See the [operator guide](../operations/offline-verification.md) for reproduction.
