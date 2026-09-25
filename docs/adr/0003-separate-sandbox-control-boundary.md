# ADR 0003 Separate Sandbox Control Boundary

- Status: accepted
- Date: 2026-09-25

## Context

Repository code, tests, build scripts, and command output are untrusted. Allowing the workflow worker to manipulate the container runtime directly expands the trusted computing base and makes policy enforcement difficult to test.

## Decision

A sandbox controller will expose a narrow protocol for create, execute, patch, diff, artifact, and destroy operations. The workflow service receives opaque sandbox identifiers and cannot request privileged runtime features.

## Alternatives

- Run commands directly in the worker process.
- Import the Docker client into the workflow service.
- Start with Kubernetes jobs.

## Consequences

The controller becomes a security-critical service with explicit abuse tests and resource policies. The boundary permits later replacement of local containers with remote workers or microVMs without changing workflow semantics.

