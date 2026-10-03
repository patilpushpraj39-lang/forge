# Forge development demo

This five-minute tour shows the working development checkpoint: saved GitHub
source, a restored review receipt and real tests around a predetermined patch.
It does not demonstrate AI solving a task, live publishing or a hosted product.
See the [readiness checklist](../verification/project-readiness.md) for the
evidence and the remaining work.

## Before the demo

- Keep the existing website and reviewer API terminals running. Use a spare
  terminal at the Forge repository root for the commands below.
- Keep general workers, publishers and paid AI execution stopped. Do not change
  credentials, payment details, profiles or the GitHub App installation.
- Use the existing completed disposable README smoke run and its saved
  `/readonly-pilot?run=...&repository=...` link. Do not create another source run,
  rerun the completed worker check or submit another review decision.
- Do not display environment files, private keys, session tokens or payment
  pages. For a public recording, hide reviewer identity, receipt/run identifiers
  and private repository details. Review captured source before sharing it.

## 1 Check the local services

In the spare terminal:

```powershell
.\.venv\Scripts\python.exe scripts/check-local.py
```

Both services should report `OK`. This checks availability only, not access,
execution or deployment. If either reports `CHECK`, stop and use the
[local health guide](local-health-check.md); do not start duplicate servers or
kill unknown processes.

## 2 Show the saved source and receipt

1. Open your saved read-only pilot link. If it is unavailable, open
   `http://localhost:3000/readonly-pilot` and enter the existing completed README
   smoke run ID and its disposable `owner/repository` from your saved run.
2. Sign in with the already approved reviewer account.
3. Click **Load preview / restore receipt**.
4. Show **Immutable source**: the repository, exact commit and source/README
   fingerprints. The captured README is data, not instructions to execute.
5. Show **Exact proposed request · not sent**, then **Approval recorded — not
   executed** with the original receipt. This is a request-review record, not
   patch approval, upload/spending consent or publishing permission.

Explain that this source run was a completed README-only smoke check. It has no
evaluated patch; no patch review or publication is expected. Do not use
**Create bounded run**, record another decision or start a worker for this tour.
If the receipt is missing or access is denied, stop rather than changing the
allowlist or bypassing sign-in. Restoration may use the local API and Clerk;
it does not send the README to an AI provider.

## 3 Show the free code and test workflow

Run this separately in the spare terminal:

```powershell
.\.venv\Scripts\python.exe scripts/check-code-workflow.py
```

This creates temporary fixture runs, not a patch for the GitHub source above.
Show these fields in the report:

- `baseline: expected_regression_failed`: the known bug was reproduced.
- `patched_public_tests: 3` and `independent_checks_passed: 4`: the fixed public
  suite and four checks in a fresh verification workspace passed for each run.
- `patch`: the predetermined change from `return value` to
  `return value.strip().lower()`, with its checked SHA-256.
- `fixture_decisions`: separate scripted approved/rejected records restored,
  without duplicate events or publication authority. They are not your approvals.
- `temporary_workspaces_destroyed: 4`, `source_unchanged: true`, and zero
  `provider_calls`, `charge_microusd` and `github_writes`.

`AWAITING_APPROVAL` is expected for these record-only fixture runs. The command
removes its temporary runs, artifacts and decisions; it does not change the
saved GitHub run or receipt. If it reports `CHECK`, stop and report the output.
Do not enable an AI provider as a workaround. Details are in the
[known-patch workflow guide](code-workflow-checkpoint.md).

## 4 Show the automated checks and close

Open [CI run 28](https://github.com/patilpushpraj39-lang/forge/actions/runs/37154522551)
for code checkpoint `6a636ef`. Show the successful `verify` and `sandbox-abuse`
jobs. This run tests the checkpoint; it is not an AI benchmark or deployment.
Any later documentation-only commit is a different revision.

Close with this explanation:

> Forge has demonstrated immutable source capture, record-only review restoration
> and a real code-and-test workflow using a fixed patch. The fixed patch is not
> AI output. Live model quality, controlled hosting and human-approved GitHub
> publishing still need separate validation.

Stop here. No new approval, paid request, branch, commit or pull request in the
disposable GitHub repository is part of this demo. These local services depend
on the PC and its server processes; `localhost` is not a public deployment.
