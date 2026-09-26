# Forge Threat Model

## Assets

- Host and sandbox-worker integrity.
- GitHub installation credentials and repository write authority.
- Model-provider credentials and spending limits.
- Private source code and benchmark content.
- Run, approval, evaluation, and audit integrity.
- Availability of the control plane and shared workers.

## Trust boundaries

1. Browser to public API.
2. API and worker to PostgreSQL and object storage.
3. Worker to model provider.
4. Worker to sandbox controller.
5. Sandbox controller to untrusted task container.
6. Publisher to GitHub.
7. Evaluator to hidden benchmark assets.

## Priority threats and required controls

| Threat | Example | Initial controls | Evidence |
|---|---|---|---|
| Sandbox escape | Malicious build accesses host | Non-root, no privilege, no host mounts/socket, syscall policy | Abuse tests and runtime inspection |
| Resource exhaustion | Fork bomb or infinite output | CPU, memory, PID, disk, output, and time caps | Bounded termination tests |
| Secret exfiltration | Test reads API key or metadata service | No control-plane secrets, denied egress, redacted environment | Secret-canary tests |
| Prompt injection | Source file asks model to ignore policy | Policy enforced outside model; repository text treated as data | Tool-policy denial tests |
| Unauthorized write | Run pushes without approval | Patch-hash approval, outbox, GitHub permission check | Integration test |
| Stale approval | Patch changes after approval | Approval binds repository, base SHA, patch SHA, action, and expiry | Mutation test |
| Duplicate side effect | Retried publish creates two PRs | Stable idempotency key and stored GitHub response | Retry test |
| Retry storm | GitHub outage causes a hot publication loop | Durable exponential retry schedule capped at five minutes | Immediate-reclaim denial test |
| Misleading cancellation | User cancels after an authorized write starts | Reject cancellation after transition to `PUBLISHING` | State-contract test |
| Benchmark leakage | Solver reads hidden tests | Separate repository and evaluator-only mount | Isolation test |
| Sensitive telemetry | Logs contain source or secrets | Structured metadata, redaction, capped artifacts | Log scan |
| Cross-run data leak | One run reads another workspace | Per-run identity, storage prefixes, cleanup, authorization | Multi-run isolation test |

## Assumptions

- The host container runtime and kernel are trusted for the first local version.
- Curated images are built from pinned definitions and scanned before release.
- The initial deployment is single-tenant and controlled by the builder.
- Reviewer identity is currently a local single-tenant claim, not an
  authenticated production identity. Internet exposure is forbidden until the
  API binds approvals to an authenticated principal.
- Model-provider handling of authorized repository content follows the configured account and API data policy.

## Out of scope for the first milestone

- A formal container escape proof.
- Multi-tenant internet exposure.
- Production incident response.
- Supply-chain attestation beyond pinned source and image digests.

These exclusions constrain deployment; they do not justify weakening the local sandbox policy.
