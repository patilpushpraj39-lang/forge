# Orphan-container cleanup verification

- Date: 2026-10-02
- Status: local unit checks passed; real Docker crash proof pending CI
- Provider calls and local container removals: none

The controller now labels each command container with a deployment scope and
hard cleanup deadline. A separate, preview-by-default tool can remove only
inspected, expired containers in its explicitly selected scope. See
[the operator runbook](../operations/orphan-container-cleanup.md).

Offline tests cover scope/deadline validation, active and foreign targets, legacy
metadata, full-ID binding, preview defaults, candidate bounds, unavailable
inspection, retryable removal failure, and audit preservation after timeouts.
Command-construction tests verify that a fractional timeout is rounded up and
the cleanup grace is added before deadline eligibility.

The local suite ran 141 tests: 130 passed and 11 skipped (4 Docker and 7
PostgreSQL). The cleanup CLI help and the repository whitespace check also passed.

The Docker CI lane includes a dedicated crash-injection check. It starts a
sandbox-owning Python process and hard-kills it after its Docker command starts,
preventing normal `finally` cleanup. The reaper must preview and then remove
only its expired container while keeping both an active same-scope neighbor and
an expired foreign-scope neighbor running. A second cleanup is idempotent.
The test uses a unique scope and disposable paths and cleans up only its own
recorded full IDs.

This new test is not yet real-Docker evidence on this workstation: Docker is
unconfigured locally, so it skips here and must pass after the commit is pushed.
Abrupt host failure, orphan filesystem removal, automatic scheduling, and
exactly-once execution are not claimed.
