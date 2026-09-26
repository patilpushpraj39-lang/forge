# Forge Publisher

The publisher is the only component allowed to write to GitHub. It consumes a
durable publication job that already references an unexpired approval for the
exact evaluated patch, reconstructs the approved changes from stored artifacts,
revalidates the installation permission and base branch head, creates a
deterministic commit and branch, and opens at most one pull request.

The branch name, commit metadata, patch hash, and publication id are stable
across retries. If a GitHub response is lost, the publisher recovers the branch
or pull request instead of creating another one.

The same service package provides the read-only GitHub catalog used by the API.
It lists active installations and accessible repositories, resolves a branch to
an exact commit, validates the recursive tree, verifies every Git blob hash, and
materializes a bounded checkout for content-addressed snapshot capture. Catalog
tokens request only metadata and contents read; publisher tokens are separate.

Production mode authenticates as a GitHub App, mints short-lived installation
tokens with metadata read, contents write, and pull-request write permissions,
and caches them only until shortly before expiry. Configure:

```text
FORGE_GITHUB_APP_CLIENT_ID
FORGE_GITHUB_APP_PRIVATE_KEY_PATH
```

Then process one queued publication with:

```text
python -m forge_publisher.main --once
```
