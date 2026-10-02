# Milestone 3 Secure Execution and Patching Verification

- Status: in progress
- Date: 2026-09-26

## Implemented evidence

| Check | Result |
|---|---|
| Deterministic snapshot artifact | Pass |
| Snapshot artifact survives sandbox destruction | Pass |
| Patch checksum binding | Pass |
| Patch path traversal rejection | Pass |
| Binary and symlink patch rejection | Implemented |
| Clean snapshot-plus-patch replay | Pass |
| Replayed diff hash equals original | Pass |
| Output-flood termination | Pass locally in under 2 seconds |
| Captured output never exceeds configured cap | Pass |
| Immutable Docker image requirement | Unit-tested |
| Docker hardening flags match machine-readable policy | Unit-tested |
| S3-compatible content-addressed artifact adapter | Pass with a deterministic fake service |
| Artifact checksum, read-cap, and tamper checks | Pass |
| Generated S3 lifecycle matches retention policy | Pass |

The local suite currently collects 32 tests. Twenty-five pass, four PostgreSQL tests
skip without a local database, and three Docker abuse tests skip without a local
Docker runtime.

## Docker abuse lane prepared for CI

The GitHub Actions job resolves the selected Python image tag to its immutable
repository digest, then exercises:

- network denial;
- host-secret non-inheritance;
- absence of the Docker socket;
- read-only root filesystem;
- bounded output, workspace growth, and process count;
- background-process teardown;
- memory-bound termination.

The job has a ten-minute outer timeout. Each individual abuse command has an
eight-second bound. Milestone 3 remains open until this job passes.

## CI diagnosis and final disk check (2026-10-02)

[CI run 9](https://github.com/patilpushpraj39-lang/forge/actions/runs/36573483264)
tested commit `03ad551`. The main verification job passed. Docker's network,
secret, root-filesystem, background-process, and memory tests passed. The output
and process checks also passed before the combined resource test failed at its
disk assertion.

Docker enforced the file-size limit (`OSError: [Errno 27] File too large`),
but the controller reported `FAILED` instead of `RESOURCE_LIMIT`. Its final
workspace measurement reused a value cached during the previous 200 ms polling
interval. The final check now always refreshes disk usage after normal or failed
command exit. The periodic running-command checks retain their polling interval.

A deterministic regression simulates a command writing up to the kernel file
limit and exiting within one polling interval. Both successful and failed exits
must report the aggregate workspace limit violation. The local suite ran 113
tests: 104 passed and 9 skipped (3 Docker and 6 PostgreSQL). No provider calls
were made. The updated real Docker CI run is still required to close this gate.

## Command cleanup on control-plane errors (2026-10-02)

Cancellation, heartbeat, and resource-measurement callbacks could previously
raise while leaving the command running. The bounded-process runner now attempts
the sandbox stop hook, terminates and reaps the local process, drains/closes its
output pipe, and re-raises the original error. A failing sandbox stop hook cannot
bypass local process termination, and the hook is attempted at most once.

Five regressions use real long-running Python child processes to verify callback
errors, stop-hook errors, and preservation of the original callback error when
cleanup also fails. Each checks process termination and output-pipe closure.
The local suite ran 118 tests: 109 passed and 9 skipped (3 Docker and 6 PostgreSQL).
No provider calls were made. These checks do not replace real Docker CI evidence
or prove cleanup when the host operating system itself denies termination.

## Docker CI gate confirmed (2026-10-02)

[CI run 10](https://github.com/patilpushpraj39-lang/forge/actions/runs/36977620664)
passed for commit `9448537`, including the main verification and Docker abuse
jobs. This closes the previously pending Docker abuse CI gate for that commit.
Later worker-recovery changes are documented in
[the recovery verification record](worker-recovery.md) and need their own CI run.
