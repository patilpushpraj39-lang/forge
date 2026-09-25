# Walking Skeleton Architecture

## Purpose

The Milestone 1 slice proves the path from run creation to durable events, repository snapshotting, bounded command execution, event replay, server-sent event delivery, and a visible run timeline.

It intentionally does not claim that the final security or persistence infrastructure exists.

## Current path

```text
Next.js run console
  -> FastAPI POST /runs
  -> local SQLite run and event store
  -> worker leases oldest CREATED or recoverable active run
  -> worker requests an opaque sandbox from the controller
  -> local controller copies the repository to a temporary workspace
  -> content-derived snapshot hash recorded
  -> controller executes fixed node --version with timeout and cancellation
  -> completion and workspace cleanup events recorded
  -> FastAPI SSE stream replays events to the console
```

## Proven properties

- A run and its events remain available after the store object is recreated.
- Event sequence numbers are monotonic and begin at one per run.
- The worker records snapshot, command-start, command-result, state, and cleanup events.
- A run cancelled before worker claim is not executed.
- An expired active lease is recovered by a different worker and records the
  ownership change as a durable event.
- Cancellation of an active command terminates its process and reaches
  `CANCELLED` within the bounded shutdown window.
- Command timeout reaches `FAILED` and releases the worker lease.
- The API and worker share only the run-store contract and persistent database file.
- The worker uses an opaque sandbox identifier and cannot access the controller's
  workspace path or process handle.
- The browser can reconnect and deduplicate replayed events by event identifier.
- The Next.js application type-checks and produces a production build.

## Deliberate temporary adapters

### SQLite

SQLite exercises transactions, persistence, replay, leases, cancellation, and
cross-process recovery without pretending to be the production database. It
must be replaced by a PostgreSQL adapter with row versions and transactional
outbox support before Milestone 1 is complete.

### Local sandbox-controller backend

The controller copies the repository to an ignored temporary directory and
destroys it after execution. This proves the create, execute, and destroy
protocol while protecting the source checkout from direct modification. It does
not isolate processes, networking, syscalls, or host resources. The
container-backed policy remains Milestone 3 work.

### Fixed command

The worker executes `node --version` with a ten-second timeout and capped output. Arbitrary repository commands are not accepted. Command policy and task-specific tool execution are later milestones.

## Remaining Milestone 1 gates

- PostgreSQL-backed run and event adapter.
- Automated live HTTP and SSE integration test in CI.
