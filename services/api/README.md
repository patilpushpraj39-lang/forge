# Forge API

The API service will own authentication boundaries, validation, task and run creation, cancellation, approvals, artifact metadata, reads, and server-sent events. It will not execute repository commands directly.

When the GitHub App variables in `.env.example` are configured, the API also
exposes a read-only installation/repository catalog and `POST /github/runs`.
That route resolves the requested branch to an exact commit, verifies and
materializes its bounded Git tree, captures a content-addressed snapshot, and
only then creates the run. GitHub credentials remain server-side.
