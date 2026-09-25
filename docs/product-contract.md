# Forge Product Contract

## User outcome

A developer gives Forge a bounded issue against an immutable repository revision. Forge returns a minimal patch, verification evidence, an independent verdict, and a human-readable explanation. The developer can approve or reject the exact patch.

## Success definition

A run is solved only when hidden acceptance checks and the required regression suite pass inside a clean verification sandbox, no hard policy or budget is violated, and the patch is reproducible from the recorded base revision.

## Initial user

The first user is the project builder running curated TypeScript and Python repositories. GitHub installation and broader user access follow only after the local execution and evaluation boundaries are stable.

## Version 0.1 capabilities

- Immutable repository snapshot and manifest.
- Targeted repository search with provenance.
- Isolated commands and patch application.
- Resumable workflow with structured events.
- Explicit resource and monetary budgets.
- Clean verification and independent evaluation.
- Live run timeline, diff, evidence, cancellation, and approval.
- Idempotent pull-request creation after approval.

## Non-goals

- General autonomous software development.
- Automatic merging or deployment.
- Production credentials inside sandboxes.
- Arbitrary repositories or unrestricted package installation.
- Multi-repository changes.
- Kubernetes before a measured need.
- Multi-agent choreography without a permission or context-isolation reason.

## Evidence required before public launch

- Versioned benchmark with at least 20 valid tasks.
- Verified solve rate with sample size and uncertainty.
- Median and p95 duration plus cost per attempt and verified solve.
- Failure taxonomy and at least one documented improvement experiment.
- Sandbox abuse-test results.
- Reproducible sample run and architecture documentation.

