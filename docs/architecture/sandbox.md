# Sandbox and Artifact Architecture

## Boundaries

Forge exposes one controller protocol for two backends:

- `LocalSandboxController` is a fast development adapter. It protects the source
  checkout but is not a host security boundary.
- `DockerSandboxController` executes each command in a new deny-by-default
  container. Snapshotting, indexing, patches, diffs, and artifacts remain
  control-plane operations outside the untrusted process.

The worker receives opaque sandbox and artifact identifiers. It never receives
the temporary host workspace path or a process handle.

## Immutable input and replay

Repository creation produces both a logical snapshot hash and a deterministic
tar artifact. Archive entries have normalized ownership and timestamps, exclude
symlinks, and are stored by SHA-256 outside the disposable sandbox directory.

Patch handling is bounded to UTF-8 unified diffs. The controller rejects path
traversal, absolute paths, `.git` writes, symlink modes, binary patches, patches
over the byte limit, and unexpected patch hashes. Generated diffs are stored as
content-addressed artifacts.

A clean verification sandbox is reconstructed from the base snapshot artifact,
then receives the recorded patch. Recreating this sequence must produce the
same diff hash.

## Docker policy

Every command receives a fresh container with:

- an image pinned by repository SHA-256 digest;
- network mode `none`;
- a read-only root filesystem and one writable workspace mount;
- all Linux capabilities dropped and `no-new-privileges` enabled;
- a numeric non-root user;
- CPU, memory, process, file-size, open-file, temporary-storage, workspace, wall
  time, and captured-output limits;
- a small `noexec`, `nosuid`, `nodev` temporary filesystem;
- no inherited host environment and no Docker socket mount;
- an init process and automatic removal after the command.

The machine-readable defaults live in
[`infra/policies/sandbox-v1.json`](../../infra/policies/sandbox-v1.json).
Docker documents the relevant runtime and security flags in its
[container run reference](https://docs.docker.com/reference/cli/docker/container/run)
and [resource constraints guide](https://docs.docker.com/engine/containers/resource_constraints/).

Using a new container per command ensures that child and background processes
cannot survive command completion. Forced cancellation, timeout, output-limit,
or workspace-limit decisions also kill the active container.

## Artifacts

The local artifact adapter writes to a SHA-256 fan-out directory. The
S3-compatible adapter uses the same SHA-256 key layout, includes checksum and
size metadata, requests server-side encryption, and tags standard-retention
objects. Artifact reads verify the identifier, expected size, checksum, and
caller-provided maximum read size. The worker records the snapshot artifact
reference as a durable event.

[`artifact-retention-v1.json`](../../infra/policies/artifact-retention-v1.json)
keeps standard run artifacts for 30 days and reserves a separate non-expiring
prefix for explicitly promoted release evidence. Deployments apply the generated
S3 lifecycle configuration to the artifact bucket.

## Remaining hardening

- Run the Docker abuse suite in GitHub Actions and record the image digest.
- Add platform-specific seccomp/AppArmor profiles where the deployment supports
  them rather than relying only on Docker defaults.
- Measure realistic language-image disk and memory budgets before public use.
