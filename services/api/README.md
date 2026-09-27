# Forge API

The API service will own authentication boundaries, validation, task and run creation, cancellation, approvals, artifact metadata, reads, and server-sent events. It will not execute repository commands directly.

When the GitHub App variables in `.env.example` are configured, the API also
exposes a read-only installation/repository catalog and `POST /github/runs`.
That route resolves the requested branch to an exact commit, verifies and
materializes its bounded Git tree, captures a content-addressed snapshot, and
only then creates the run. Catalog access and GitHub-backed run creation require
an authorized reviewer session. GitHub credentials remain server-side.

Reviewer actions are disabled by default. Set `FORGE_AUTH_MODE=clerk` together
with the Clerk public JWT key, authorized frontend origins, and
`FORGE_REVIEWER_IDS` from `.env.example` to enable them. The API accepts only
verified session tokens, derives the approval actor itself, and requires the
same actor to publish an approval. It never persists bearer tokens.

When `FORGE_BENCHMARK_RECORDS_PATH` points to reviewed JSONL evidence, the API
revalidates and recomputes the latest benchmark summary for the failure-analysis
dashboard. Category and split filters are applied to the original records, not
to pre-aggregated percentages. The API never exposes the configured local path.
