# Free development verification checkpoint

Date: 2026-10-03. Status: local regression and packaged-web checks passed;
this is not a deployment or live model-quality result.

## Verified at this checkpoint

- The complete Python suite ran 168 tests: 156 passed and 12 skipped.
  Eight PostgreSQL integration cases and four Docker integration cases were
  skipped because their dedicated local test configuration was absent. Skips
  are not passes; historical CI reproduction is recorded separately.
- All 30 web tests passed: 20 transport cases, seven reviewer-status cases and
  three safe GitHub catalog error-message cases.
- TypeScript checking, scaffold validation and diff whitespace checks passed.
- An optimized Next.js 16.3.6 Turbopack production build passed in a separate
  ignored staging directory, using the existing installed dependencies. No
  installation, upgrade or replacement of dependencies was performed.
- The staged standalone server returned HTTP 200 for `/`, `/demo`, `/benchmarks`
  and a static asset referenced by the home page. It used an OS-selected loopback
  port and was stopped afterward. No engineering run or demo was submitted to
  that server. The user's normal development servers were not stopped.
- The preceding development browser check verified the optional GitHub warning
  appears only in the GitHub selector; deselecting it removes that warning and
  re-enables the local form. Rapid select/deselect also did not leave loading
  stuck. The existing authenticated reviewer identity remained visible.
- The user's earlier sign-out/sign-in and signed-in offline-demo checks are
  recorded in [authenticated web verification](authenticated-web.md).

## Isolation and limits

Inherited Forge, OpenAI, Clerk, Anthropic and AWS settings were removed from the
verification child processes. The staged build copied source/configuration only,
not `.env.local` or other credentials. Next.js and Clerk telemetry were disabled
for the build and standalone smoke check. The original web build directory and
the user's generated `next-env.d.ts` were left unchanged.

The production build and standalone check used unconfigured Clerk and the default
development web profile, matching the credential-free CI build. They do not prove
production Clerk sign-in, controlled-profile approval, HTTPS integration, or that
a publicly deployed site works. The route smoke check verifies serving, not
backend execution. GitHub CI reproduction of `733ce1d` subsequently passed as
recorded below; this does not cover later changes automatically.

No paid inference, live benchmark, GitHub API write, credential configuration,
billing change or public deployment was performed during this verification.

## Remaining work before public live use

1. Finish controlled-profile authentication and GitHub App integration evidence.
2. Verify hosting, HTTPS, storage/recovery, rate limits and security boundaries;
   do not expose the trusted local development API publicly.
3. Only after separate cost approval, enable live inference and execute the
   frozen benchmark/experiments. Offline workflow evidence must not be presented
   as autonomous model performance.

Paid inference and deployment remain deferred at the owner's request. The free
offline workflow can continue without either.

## GitHub reproduction of this checkpoint

[CI run 37109800643](https://github.com/patilpushpraj39-lang/forge/actions/runs/37109800643)
(CI #18) at `733ce1d` passed both `verify` and `sandbox-abuse` on 3 October 2026.
The owner supplied the success screenshot; job metadata and decoded logs were
then independently read through the GitHub connector without rerunning jobs.

- Verify job: 168 Python tests, 164 passed, four Docker integration skips.
  All eight PostgreSQL cases executed and passed, including patch-bound approval
  and the publication outbox. Migrations, invalid-ID handling and fixture cleanup
  were exercised.
- All 30 web tests, TypeScript checking, scaffold validation, frozen-lockfile
  dependency installation and the production build passed.
- The separate Docker job ran nine sandbox policy/abuse cases and 15 recovery
  cases, all passing without skips, including the expired-container crash test.

The four skips in the verify job are not counted as passes there; their coverage
comes from the separate Docker job. Live Clerk/GitHub integration and benchmark
performance remain open. The test suites use fixtures/mocks for provider and
GitHub behavior, not paid inference or repository publication.

## Local health-check follow-up

The subsequent `scripts/check-local.py` addition provides a standard-library,
read-only serving check. Seven regression cases cover both expected responses,
unauthenticated GET-only requests, ignored proxies/credentials, malformed health
responses, wrong application pages, redirects, oversized responses, sanitized
HTTP/network failures, CLI output/exit codes, and invalid ports.

The expanded local Python suite ran 175 tests: 163 passed and the same 12
environment-dependent cases skipped. Scaffold and whitespace checks passed.
The command was also run against the owner's existing development services:
both returned expected HTTP 200 responses, with exit code 0 in text and JSON
modes. It did not start/stop processes or submit a run. These new changes have
not yet been reproduced in GitHub CI; the preceding CI evidence remains bound
to `733ce1d`, not automatically to this follow-up.
