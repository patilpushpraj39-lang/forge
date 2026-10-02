# Expired sandbox-container cleanup

This is an opt-in operator tool for the Docker backend. The offline local demo
does not need it. It makes no provider calls and does not publish to GitHub.

## Ownership and deadline

Every Docker command launched by the controller carries:

- `forge.sandbox=true`;
- `forge.cleanup.scope`, matching the deployment's `FORGE_SANDBOX_SCOPE`;
- `forge.cleanup.deadline`, an integer Unix timestamp rounded up from command
  launch time plus its command timeout plus a 30-second grace period.

Use a unique scope per deployment sharing a Docker daemon. `development` is a
local default, not a production deployment identifier. Containers without the
new labels are treated as legacy resources and left untouched.

The cleanup tool lists by both ownership and scope labels, then inspects each
full container ID. It independently checks those labels, the expected
`forge-exec-<UUID>` name, and the expired deadline before removing anything.
It never runs a broad Docker prune or follows a mount path to delete host files.
The inspection template excludes container environment variables.

Docker supports these operations through
[label filters](https://docs.docker.com/reference/cli/docker/container/ls/),
[formatted inspection](https://docs.docker.com/reference/cli/docker/container/inspect/),
and [removal by container ID](https://docs.docker.com/reference/cli/docker/container/rm/).

## Preview first

From the Forge checkout, use the exact scope configured for that deployment:

```powershell
.\.venv\Scripts\python.exe scripts/reap-sandboxes.py --scope development
```

The default is preview only. Review the JSON `eligible`, `skipped`, and
`failures` fields. An eligible container is past its allowed execution deadline,
not merely idle or missing a heartbeat. Healthy containers within their deadline
remain untouched.

Only after verifying the scope and candidate IDs, opt into removal:

```powershell
.\.venv\Scripts\python.exe scripts/reap-sandboxes.py --scope development --execute
```

`removed` lists successful removals by immutable full ID. Failed or timed-out
removals are reported, not claimed successful; rerun a preview before retrying.
Already auto-removed containers may show `inspection_unavailable`. The command
exits nonzero for reported failures. Candidate validation is bounded to 100
unique IDs by default, each Docker request to at most 10 seconds, and an entire
scan to 60 seconds. If the candidate bound is exceeded, no removals occur.

## Deployment limits

Run cleanup from a separately supervised process; a crashed worker cannot clean
up itself. No recurring job is created or enabled by this implementation.
Container cleanup is therefore not automatic until an operator integrates it
into deployment operations.

This control trusts the Docker daemon and host clock. It does not protect against
a host administrator forging labels, a materially incorrect clock, or an
unavailable daemon. Never give repository code access to the Docker socket.
The local adapter still offers no host-security isolation.

This tool does not delete abandoned workspace directories, retained artifacts,
images, or volumes. Filesystem garbage collection requires separate ownership
records and path validation and remains a follow-up.
