# Verify the free offline workflow

This checks the engineering workflow, not autonomous model quality. No API key
or paid provider call is required. A demo review is record-only and cannot
authorize a GitHub write. The local execution backend is not a security sandbox;
use only the bundled trusted fixture in this mode. Do not expose this development
API publicly.

## Automated restart and integrity check

For a temporary code-and-test run that prints baseline failure, repaired tests,
the exact diff and both scripted record-only decision paths, use the
[known-patch code workflow checkpoint](code-workflow-checkpoint.md). It leaves
the normal demo database and any saved GitHub source receipt untouched.

From the Forge repository in PowerShell, run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_offline_demo_http.py -v
```

The expected result is `Ran 3 tests` followed by `OK`. The tests cover:

- Both approve and reject decisions surviving a full API process restart.
- Retry replay recording only one decision; conflicting decisions returning 409.
- Stale patch/verdict hashes returning 409 and malformed input returning 422.
- Altered or missing persisted patch files blocking review and decision writes.
- Disabled reviewer authentication blocking a publication request, with no
  approval or publication events created.

The test server binds an OS-selected loopback port, not your usual 8000. Each
test uses a disposable SQLite database, artifact directory, and workspace root.
It removes inherited Forge configuration and OpenAI/Clerk/AWS/Anthropic
environment variables from the server process, disables system HTTP proxies
for its test client, and selects only the fixed offline runtime. Your running
demo, normal database, clipboard, and GitHub credentials are not modified.
Test processes stop before disposable directories are cleaned up.

These tests run automatically in the normal GitHub Python verification suite.
To run the full local suite instead:

```powershell
.\.venv\Scripts\python.exe scripts/run-python-tests.py
```

Docker and PostgreSQL tests may skip locally when their dedicated test
configuration is absent. Skips are not passes.

## Optional browser check

1. Keep the normal API and web development servers running.
2. Open `http://localhost:3000/demo` and click **Run real offline demo**.
3. Confirm that the patch and four independent checks appear.
4. Click **Approve locally** or **Reject**.
5. Refresh the page and confirm the same decision is restored.

When Clerk is configured, start the web server with `--hostname localhost` and
use `localhost` consistently in the browser. Do not mix that browser origin
with a web server bound to `127.0.0.1` during the Clerk handshake.

The automated check additionally verifies persistence across API restarts,
which refreshing the browser alone cannot prove. It does not automate browser
rendering or establish that a deployed installation works.

## Download a review report

After the patch and checks appear, click **Download offline evidence**. Your
browser saves `forge-offline-demo-report.json` to its download location. You can
download before deciding (the report has `decision: null`) or after deciding
(the report includes the saved approval/rejection and its exact evidence hashes).
Downloading does not make or change a decision and sends no additional API request.

The browser recomputes SHA-256 over the exact UTF-8 patch, including its trailing
newline, before saving. A mismatch or inconsistent decision prevents export and
shows a safe message; reload the demo rather than bypassing that check. The full
verdict artifact is not included, so its hash is a backend reference, not an
independently verified verdict hash in this file. Raw events, actors, retry keys
and environment configuration are excluded through a field whitelist.

The report is editable JSON, not a signed attestation. It documents this scripted
public-fixture workflow only, not live model quality, benchmark results, security
isolation or production readiness. Runtime external-request counts do not include
browser traffic to the local API or optional authentication service. Keep private
benchmark tasks and answer patches separate; this export is for the offline demo,
not arbitrary engineering runs.

## If a check fails

- An API startup failure: confirm the Python environment has the repository's
  API and worker dependencies installed. The test does not need your normal
  development server to be running.
- A demo run failure: use the reported test assertion to identify the failed
  stage. Do not configure a paid provider to repair an offline test.
- A patch-integrity error in the normal demo: do not approve that run. Create a
  fresh offline demo; investigate missing or altered artifacts separately.

Never paste API keys, access tokens, or private benchmark answer patches into
failure reports.
