# Forge Sandbox Controller

The sandbox controller is a separate trust boundary. It exposes bounded create,
index, search, dependency-neighborhood, context-pack, execute, and destroy
operations without exposing filesystem paths or process handles to the workflow
worker. Patch, diff, and artifact operations follow in Milestone 3.

The local backend copies a repository into a disposable workspace and enforces
timeout, cancellation, and streaming output limits. It proves the protocol but
is not a host security boundary.

The Docker backend creates a fresh hardened container per command using an
immutable image digest, a read-only root filesystem, no network, no Linux
capabilities, a non-root user, and explicit CPU, memory, process, disk, output,
and time bounds. Containers receive only the disposable workspace mount and a
minimal environment.

Both backends support deterministic content-addressed snapshots, safe unified
patch application, diff artifacts, and clean snapshot-plus-patch replay. See
[the sandbox architecture](../../docs/architecture/sandbox.md).

Artifacts can use the local SHA-256 store or an S3-compatible bucket. The S3
adapter verifies content on read, requests server-side encryption, and produces
the lifecycle configuration defined by the repository retention policy.
