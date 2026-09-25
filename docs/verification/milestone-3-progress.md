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

The local suite currently collects 28 tests. Twenty-one pass, four PostgreSQL tests
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
eight-second bound. Milestone 3 remains open until this job passes and the
remaining object-store/retention decision is recorded.
