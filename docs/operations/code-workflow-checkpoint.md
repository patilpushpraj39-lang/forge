# Free code-and-test workflow checkpoint

From the Forge repository in a spare terminal, run:

```powershell
.\.venv\Scripts\python.exe scripts/check-code-workflow.py
```

Keep the existing website/backend terminals running. No restart or profile,
credential, billing or GitHub App configuration change is needed.

## What it runs

This reuses Forge's retired public status-normalizer fixture and fixed offline
runtime. It is **not AI solving a task**. The worker really reads source, runs
tests, applies a predetermined one-line patch, captures the diff and submits
it to independent evaluation in a fresh local workspace.

The command validates pinned hashes of the three fixture files before copying
or executing them. It accepts no arbitrary repository, approval, publication or
AI-enable option. A separate Python child removes inherited service/credential
settings and puts its database, artifacts and all worker/evaluator allocations
in a temporary directory. The child blocks connection initiation and DNS through
the shared checkpoint guard. The fixed, hash-checked public Python commands
are the only repository code executed; they make no provider/network calls.
No system package installation, API server, Docker service or publisher starts.
The exact running Python interpreter is used for tests, not an unrelated binary
named `python` on PATH.

The helper has a 60-second deadline. On Windows, interruption/timeout cleanup
targets only the helper's own process ID and descendants, not processes named
`python` or `node` and not the running Forge terminals.

## Expected evidence

- `baseline: expected_regression_failed`: the original whitespace/case test
  failed, with exactly one known failure and no truncated output.
- `patched_public_tests: 3`: the repaired public suite passed.
- `independent_checks_passed: 4`: public regression, acceptance smoke, invalid
  value guard and syntax check passed in a workspace distinct from the solver.
- `patch` and `patch_sha256`: the exact review diff and recomputed digest.
- `fixture_decisions`: separate temporary approved/rejected records restored
  after reopening the store; exact replay created no duplicate event and stale
  patch/verdict or conflicting decisions were rejected without audit changes.
- Both runs remain `AWAITING_APPROVAL`; these scripted record-only decisions
  grant no publication authority. No patch approval or publication job exists.
- `temporary_workspaces_destroyed: 4` and `source_unchanged: true`.
- Zero provider calls, charges and GitHub writes.

The change shown for review is:

```diff
-    return value
+    return value.strip().lower()
```

The JSON report is printed in the terminal, not saved as a signed attestation.
It excludes temporary paths, actor IDs, receipt IDs and raw command output.
Temporary runs, artifacts and decisions are removed when the command exits.
Your existing GitHub source run and signed-in read-only receipt are untouched.
The fixture decisions are scripted tests, **not approvals by your account**.

Local temporary workspaces are not host security sandboxes; this is appropriate
only for the bundled trusted fixture. Passing does not prove live AI quality,
hidden benchmark success, a Docker/production security boundary, deployed access
control, actual GitHub source execution or publication readiness. Paid AI remains
disabled. CI repeats this same temporary workflow.

If the command returns `CHECK`, do not bypass its fixture/integrity gates or
configure a paid provider. Send the safe terminal result for diagnosis.
