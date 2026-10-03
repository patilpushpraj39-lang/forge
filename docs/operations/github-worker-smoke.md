# Local GitHub snapshot worker check

This is a no-model, no-publisher smoke check for one disposable repository run.
It verifies that the worker can restore the saved source snapshot, index it,
read `README.md`, record the result, and remove its temporary workspace. It does
not prove AI task quality, patch evaluation, human approval, or publication.

## Before starting

Keep the local website and reviewer API running. Keep general workers and
publishers stopped. Use a disposable, trusted, README-only GitHub repository
installed for the pilot App. Do not use real customer code for this check.
The helper requires local development mode, SQLite and local artifacts/sandbox;
it refuses controlled mode, PostgreSQL, Docker and S3 settings.

## Steps

1. Open Forge and select **Use an installed GitHub repository**. Select the
   disposable repository and its `main` branch.
2. Paste this exact engineering task:

   ```text
   Worker smoke test only: read the captured README. No AI, file changes, or pull request.
   ```

3. Click **Create bounded run** once. Confirm the new state is `CREATED` and the
   audit trail includes `github_source_ingested`. Copy the new run ID from the
   page URL. Do not reuse a cancelled or previously started run.
4. Open a separate PowerShell terminal at the Forge project root. Replace both
   placeholders below with the new ID and the selected disposable repository:

   ```powershell
   .\.venv\Scripts\python.exe scripts/check-github-worker.py --run-id "NEW-RUN-UUID" --repository "OWNER/TEST-REPOSITORY"
   ```

5. The helper exits after this one check. On success it prints `COMPLETED`.
   Refresh that run in Forge and inspect `snapshot_ready`, `repository_indexed`,
   `command_completed`, and `workspace_destroyed`. The command result contains
   README byte count and SHA-256, not the README contents. No approval or publish
   step is expected.

## Safeguards and limits

The helper uses the existing ignored `.env.reviewer.local` for path/profile
settings without loading it into the process environment or opening a private
key. Storage must already exist inside this project. Only the explicitly named
run with the exact task and repository is eligible. Its snapshot is checked
through the existing artifact size/hash validation before the command executes.

The command is fixed, uses isolated Python, reads at most 2,000,001 README bytes,
and has a ten-second execution timeout. Inherited credentials are stripped while
the worker runs. It does not instantiate a model runtime or publisher, execute
repository scripts, fetch GitHub data, or change the live repository. Local
database audit/state updates and temporary workspace/index artifacts are
expected. The existing claim routine can also finalize abandoned cancellations;
it does not select unrelated queued tasks for execution.

This local sandbox is not a production security boundary. Run this helper only
with the trusted disposable pilot repository. If the check fails, inspect the
selected run's audit trail; do not start a general worker or change credentials
as a workaround. This check is not a deployment-readiness gate.
