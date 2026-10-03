# Offline evidence download verification

Date: 2026-10-03. Scope: browser-only export from the fixed, deterministic public
demo. No new backend endpoint or package dependency was introduced.

## Verified locally

- All 38 web tests passed: the existing 30 and eight export cases covering
  pending and both saved decisions, field whitelisting, zero-usage/offline
  restrictions, exact UTF-8 patch hashing, stale decisions, malformed fields,
  relative paths and independent copies of check/path arrays. Failed check
  statuses are preserved, never rewritten as passing.
- TypeScript checking, scaffold validation and diff whitespace checks passed.
- An optimized Next.js 16.3.6 build passed in a fresh ignored staging directory
  with existing dependencies. No environment files were copied; inherited
  provider/authentication settings were removed and telemetry disabled in the
  build child process. The owner's running services and build directory were
  not replaced. This credential-free build does not establish production auth.
- The existing development browser demo restored its already-approved run.
  Clicking **Download offline evidence** saved a 2,737-byte JSON file in the
  browser's download location without starting a new run or changing its decision.
  The browser automation's download-event wait timed out, but the actual file
  existed and was independently inspected on disk; the UI showed no export error.
- That downloaded file contained four checks, the saved approval and matching
  decision/patch/verdict references. SHA-256 was independently recomputed over
  its exact UTF-8 patch and matched the exported hash. Raw event fields, actors,
  retry keys, environment settings and the owner's Clerk identity were absent.
  The report retained zero model calls and no GitHub-write permission.

The browser check covers the owner's restored approval. Pending and rejected
exports and failure cases were verified by unit tests, not additional browser
decision writes. The UI error path was not fault-injected in the running API.

## Evidence limits

The exported verdict hash is a backend artifact reference; the full verdict
artifact is not exported or independently hashed by the browser. Check statuses
and the saved decision come from the local backend. JSON can be edited, so this
is not a signed attestation or portable authorization token. Explicit disclosures
identify the deterministic public fixture and exclude claims of live AI quality,
benchmark performance and deployment readiness.

No paid model call, provider configuration, private benchmark export, GitHub
write, public hosting or billing change was made. The backend Python suite was
not rerun for this client-only change. New code awaits GitHub CI reproduction
after the owner pushes it; the previous green CI run covers `cc70223` only.
