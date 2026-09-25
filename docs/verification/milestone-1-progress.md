# Milestone 1 Progress Verification

- Status: in progress
- Date: 2026-09-25

## Passing evidence

| Check | Result |
|---|---|
| Scaffold contract | 15 required files pass |
| Local store survives recreation | Pass |
| Persisted event replay | Pass |
| Worker snapshot, command, completion, and cleanup sequence | Pass |
| Pre-claim cancellation | Pass |
| Live API health | HTTP 200 |
| API run creation | HTTP 201 |
| API-to-worker final state | `COMPLETED` |
| Persisted events in live run | 8 events |
| SSE replay | All 8 events observed in order |
| Web TypeScript check | Pass |
| Web production build | Pass |

## Live run event sequence

```text
run_created
state_changed CREATED to SNAPSHOTTING
snapshot_ready
state_changed SNAPSHOTTING to EXECUTING
command_started
command_completed
state_changed EXECUTING to COMPLETED
workspace_destroyed
```

## Not yet proven

- PostgreSQL behavior and migration safety.
- Lease expiry and crash recovery while a step is active.
- Cancellation of an already running command.
- Process, filesystem, network, and resource isolation.
- End-to-end browser behavior under disconnect and reconnect.

The milestone remains open until these gates are implemented and verified.

