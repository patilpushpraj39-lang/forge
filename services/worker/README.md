# Forge Worker

The worker advances durable run state through leased, versioned transitions.
It selects the local or Docker sandbox backend from explicit environment
configuration and records immutable snapshot artifact references in the event
timeline. Each step has a stable owner, bounded execution, and an explicit
failure reason.

`ForgeToolExecutor` is the model-facing boundary. It exposes only versioned,
strictly validated operations for repository manifests, search, bounded file
reads, dependency lookup, allowlisted commands, checksum-bound patches, and
content-addressed diffs. Package installation and arbitrary executable paths
are denied by the first command policy.
