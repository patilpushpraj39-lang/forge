# Walking Skeleton Architecture

## Purpose

The Milestone 1 slice proves the path from run creation to durable events, repository snapshotting, bounded command execution, event replay, server-sent event delivery, and a visible run timeline.

It intentionally does not claim that the final security or persistence infrastructure exists.

## Current path

```text
Next.js run console
  -> FastAPI POST /runs
  -> PostgreSQL run state, append-only events, and transactional outbox
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

### PostgreSQL source of truth

PostgreSQL uses row-level locking with `skip locked` for parallel worker claims,
short transactions for state transitions, lease expiry for recovery, row
versions for observability, and a transactional outbox for reliable future
fan-out. Ordered migrations are checksum-protected and safe to apply
concurrently. Connection pooling bounds database connections per process.

### SQLite development adapter

SQLite exercises transactions, persistence, replay, leases, cancellation, and
cross-process recovery without pretending to be the production database. It is
kept for quick local development and the fast unit-test lane.

### Local sandbox-controller backend

The controller copies the repository to an ignored temporary directory and
destroys it after execution. This proves the create, execute, and destroy
protocol while protecting the source checkout from direct modification. It does
not isolate processes, networking, syscalls, or host resources. The
container-backed policy remains Milestone 3 work.

### Fixed command

The worker executes `node --version` with a ten-second timeout and capped output. Arbitrary repository commands are not accepted. Command policy and task-specific tool execution are later milestones.

## Remaining Milestone 1 gates

- Execute the PostgreSQL and live HTTP/SSE integration job in CI.
