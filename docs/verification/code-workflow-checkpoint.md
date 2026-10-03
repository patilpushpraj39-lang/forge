# Known-patch code-and-test checkpoint

Date: 2026-10-04. Scope: real local execution of a hash-pinned public fixture with
a predetermined patch and scripted temporary decisions, not model performance.

The owner approved preparing this free workflow after CI #27 at `2cc119f` passed
and user-provided screenshots showed signed-out reviewer restrictions followed
by restoration of the original read-only receipt after signing in. That browser
evidence covers the local development setup, not a controlled deployment.

`scripts/check-code-workflow.py` reuses the existing offline runtime, worker,
tool controller, independent evaluator and demo review/record-only handlers.
No new application execution or approval API is introduced. Source validation,
command/patch allowlists and review digest comparison constrain this command to
the known public fixture. The captured diff omits the optional function-context
text in the supplied patch's hunk header; both accepted encodings describe the
same predetermined one-line change.

Two separate temporary runs test approved and rejected records. Each run must
produce exactly six command results: expected failing regression, repaired
public suite and four successful independent checks in a different workspace.
Both source copies and the original bundled fixture stay unchanged. Both solver
and evaluator workspaces must be destroyed for each run. Patch/verdict mismatch,
decision conflicts and exact replay are checked against the durable audit trail.
Reopening the SQLite store must restore the same review and decision without a
new event. Approval/publication tables must remain empty; the runs must remain
awaiting review rather than published or completed.

The report contains the exact diff and its SHA-256, checks and scoped fixture
decision outcomes. It omits raw source paths, actors, receipt identifiers and
test output. Scripted records are not human-approved patches. The command creates
no report file or persistent ledger and makes no normal-run updates.

The child uses a temporary working directory, isolated Python import mode and
the existing environment/network guards. Known fixture subprocesses inherit the
scrubbed environment; their pinned source contains no network/provider code.
The guard in the parent fixture interpreter is not an operating-system sandbox
for child processes. Public fixture execution and mocked/fixture access evidence
must not be presented as untrusted-code isolation or live AI-quality evidence.

Current backend/frontend processes, real GitHub repository, stored source run,
read-only receipt, account configuration and paid-execution settings are unchanged.
Real model execution, controlled hosting, PostgreSQL/Docker integration for this
command, multi-tenant access and publication remain separate gates.

Local verification on 2026-10-04: the standalone command passed, as did all
10 checkpoint regression tests on Windows. The full Python suite discovered
296 tests: 283 passed and 13 PostgreSQL/Docker infrastructure checks skipped
(not passes). All 50 web tests, scaffold validation and whitespace checks passed.
Timeout/interruption tests mock termination and check that only the launched
helper is targeted; they are not proof of every operating-system failure mode.
CI reproduction of this new command remains pending. The existing generated
`apps/web/next-env.d.ts` change and running website build output were preserved.
