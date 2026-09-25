# Forge Security Policy

Forge executes untrusted repository code. Treat every repository, dependency, test, build script, command output, and model-generated instruction as hostile input.

## Reporting a vulnerability

Do not open a public issue for a vulnerability that could enable sandbox escape, secret exposure, unauthorized repository writes, cross-tenant data access, or denial of service. Report it privately to the project maintainer through the security contact configured on the eventual public repository.

No security contact is published in this local scaffold. Add one before the repository becomes public.

## Non-negotiable controls

- No privileged task containers.
- No host network, host PID namespace, host filesystem mounts, or container-runtime socket.
- No control-plane secrets inside task sandboxes.
- Default-deny outbound network access.
- Explicit CPU, memory, process, disk, output, and wall-clock limits.
- Human approval bound to the exact patch hash before an external write.
- Structured audit events for commands, patches, evaluations, approvals, and publishing.

See [the threat model](docs/security/threat-model.md).

