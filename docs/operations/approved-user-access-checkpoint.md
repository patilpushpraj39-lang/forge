# Approved-user access checkpoint (free, isolated)

This checkpoint checks the controlled API's access policy for read-only preview
and record-only decisions. It does **not** put the current localhost servers in
controlled mode or enable AI execution. Keep both existing servers running.

## Automatic fixture check

From the Forge repository in a spare terminal:

```powershell
.\.venv\Scripts\python.exe scripts/check-controlled-access.py
```

The command launches an isolated child process in a temporary directory, removes
inherited Forge/OpenAI/Clerk/AWS/Anthropic and public web settings, and runs three
required test groups. Networking is blocked before product imports; Python's
internal loopback socketpairs are allowed for Windows event-loop wake-ups, not
connections to existing services. It does not load credential/configuration files
or use the real source run, database, artifact store or GitHub App key. Temporary
SQLite fixtures and locally generated signing keys are used instead.

The real Clerk SDK verifies those locally signed test sessions. Controlled
middleware and the actual preview/decision handlers are exercised in-process.
Configuration uses an example HTTPS origin, database URL and image digest; the
test does not connect to PostgreSQL/Docker, bind an HTTPS server or start workers.
Passing requires every discovered check to pass with zero skips, and every
required test group must be present. The command is included in CI.

Covered checks:

- Approved fixture sessions load the canonical preview and restore receipts.
- Missing, malformed, expired, future-dated, pending, wrong-origin and
  wrong-signature sessions are denied; non-allowlisted subjects are forbidden.
- Denial happens before source-run reads, artifact reads or decision writes.
- URL/body credentials cannot replace bearer authentication; the browser cannot
  supply reviewer identity or paid-execution authority.
- Verifier unavailability fails closed. CORS preflight does not authenticate.
- Same-reviewer exact replay is idempotent. Another approved reviewer cannot
  replay or overwrite the recorded decision under their own identity.
- Source rows are unchanged; no patch-approval or publication job is created;
  the controller performs artifact reads only.

Controlled mode is **single-tenant**: allowlisted reviewers share read access to
that tenant's source and receipts. This is not per-user ownership or multi-tenant
isolation. The preview's approval is record-only, never a patch or spending grant.

## Manual development-browser check (no new approval)

This checks the existing local development app, not a controlled deployment:

1. Open the existing `/readonly-pilot?run=...&repository=...` link, sign in as the
   already approved reviewer, and load the preview. Confirm the original receipt
   ID and timestamp are restored. Do not submit another decision.
2. Sign out using the account menu at the top of the page. The README, exact
   request and receipt must disappear, and preview/decision controls must not
   allow protected requests. Scroll down to check that old evidence is gone.
3. Refresh while signed out. No preview/receipt should be visible. Do not paste
   a token or sign-in ID into any URL, command or chat message.
4. Sign back into the same approved account and load the preview. The original
   receipt should return unchanged, with "Approval recorded — not executed."
5. Report the result or a screenshot without credentials. If evidence remains
   visible after sign-out, stop here and report the failure.

A real unapproved-account browser check remains pending unless the operator has
an existing second account they choose to use. Do not change the allowlist or
create an account just for this checkpoint; fixture denial is not live-account
proof. Real session expiry, account switching, HTTPS/CORS/proxy behavior,
PostgreSQL/Docker readiness and deployed controlled-profile integration remain
separate gates. Do not expose the development API publicly or switch profiles.

No payment method is required for these checks. Paid inference and GitHub
publication stay disabled/not invoked. See the
[controlled API prerequisites](controlled-api.md) before any later deployment.
