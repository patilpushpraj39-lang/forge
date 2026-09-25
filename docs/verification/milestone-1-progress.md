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
| Expired active lease recovery by a second worker | Pass |
| Active command cancellation and process termination | Pass, worker stopped in under 2.5 seconds |
| Command timeout and lease release | Pass |
| Opaque sandbox-controller create/execute/destroy protocol | Pass |
| Source checkout unchanged after local sandbox execution | Pass |
| Live API health | HTTP 200 |
| API run creation | HTTP 201 |
| API-to-worker final state | `COMPLETED` |
| Persisted events in live run | 8 events |
| SSE replay | All 8 events observed in order |
| Browser run creation and live timeline | Pass |
| Browser reload and durable history restoration | Pass |
| Web TypeScript check | Pass |
| Web production build | Pass |

The browser verification used run `67121173-c33e-401a-b986-9ad9646caa2a`.
Its identifier remained in the URL, the final state restored as `COMPLETED`,
and all eight persisted events replayed after a full page reload.

The sandbox-controller CLI verification used run
`429e47a1-705e-43ba-8868-9276d42dcdcc`. The worker received only sandbox ID
`d721bff0-719e-491b-97bb-c97128900b40`, completed through the controller, and
recorded destruction of that same opaque handle as event eight.

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

## Remaining Milestone 1 evidence

- PostgreSQL behavior and migration safety.
- Automated live HTTP and SSE integration coverage in CI.

The milestone remains open until these two gates are implemented and verified.

## Deferred security evidence

Process, filesystem, network, syscall, and resource isolation are deliberately
deferred to the container-backed sandbox work in Milestone 3. The current local
controller is a protocol proof, not a security boundary.
