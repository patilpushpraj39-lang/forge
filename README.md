# Forge

Forge is a sandboxed autonomous software-engineering platform. It accepts a bounded task against an immutable repository snapshot, investigates the codebase, proposes and applies a patch inside an isolated environment, verifies the result, evaluates it independently, and asks a human to approve the exact patch before any GitHub write.

This repository is the public product monorepo. Hidden benchmark tests and answer patches are maintained separately so released evaluation results are not contaminated by test leakage.

## Current status

Milestones 0 and 1 are complete. Milestone 2 repository intelligence is
implemented and reproduced in GitHub CI. Milestone 3 secure execution
has passing Docker abuse and expired-container crash-cleanup checks. Milestone
4's bounded-agent foundation is active, Milestone 5 independent evaluation
is complete locally, and the Milestone 6 approval/publishing foundation is
implemented. The product contract, architecture decisions,
threat model, repository structure, initial contracts, CI scaffold, three
seed fixtures, the separate balanced 20-task private benchmark, and the
executable walking skeleton have
been validated from clean environments.

See [the Milestone 0 verification record](docs/verification/milestone-0.md) and
[the Milestone 1 verification record](docs/verification/milestone-1-progress.md).

Milestone 1 delivered a tested local persistence adapter, a PostgreSQL
source-of-truth implementation, leased worker
recovery, bounded active-command cancellation, an SSE API, and a
production-building Next.js console that restores run history after reload. The
worker accesses disposable execution only through an opaque sandbox-controller
protocol. The first controller backend is local and is not a security sandbox.
SQLite remains available as a zero-dependency development adapter. PostgreSQL
adds row versions, non-blocking concurrent claims, migration checksums, partial
indexes, connection pooling, and a transactional outbox. Its real integration
suite passed against PostgreSQL 17 in GitHub Actions. See [the
walking-skeleton design](docs/architecture/walking-skeleton.md).

The repository-intelligence layer now creates ignore-aware manifests, detects
languages and build systems, extracts Python and TypeScript symbols/imports,
supports structural and lexical retrieval, and produces size-bounded context
with snapshot and line provenance. Its objective ranking found the relevant
production source file for all five private seed tasks within the top three
results without reading hidden tests or answer patches. See the
[repository-intelligence architecture](docs/architecture/repository-intelligence.md)
and [Milestone 2 verification](docs/verification/milestone-2.md).

Milestone 3 secure execution and patching is active. The controller now creates
deterministic content-addressed snapshots, validates and applies bounded unified
patches, generates reproducible diff artifacts, and reconstructs clean
verification sandboxes. A Docker backend with default-deny networking and
explicit CPU, memory, process, filesystem, output, and time limits has passed
the GitHub abuse-test lane. Content-addressed artifacts can use a local
store or an S3-compatible encrypted bucket with explicit retention. See the
[sandbox architecture](docs/architecture/sandbox.md) and
[Milestone 3 progress record](docs/verification/milestone-3-progress.md).

The Milestone 4 foundation adds a provider-neutral model runtime, an OpenAI
Responses API adapter, strict serial tool schemas, code-enforced token/cost/time
and action budgets, durable idempotency records, bounded repository and sandbox
tools, retries, explicit stop reasons, and sanitized provider-error evidence.
Known billing, quota, and spend-limit errors stop without wasteful retries.
Runs now persist their objective and
exact budgets, and the worker can execute the loop through an opt-in agent path
that ends with a content-addressed patch awaiting approval. A deterministic
private five-task orchestration proof passes, but it uses answer patches and is
not a model-quality result. The live-model benchmark remains the milestone exit
gate. See the
[agent-loop architecture](docs/architecture/agent-loop.md) and
[Milestone 4 progress record](docs/verification/milestone-4-progress.md).

Milestone 5 adds a solver-independent evaluator between patch generation and
human approval. It reconstructs the immutable snapshot in a fresh sandbox,
applies the exact content-addressed patch, enforces patch policy, runs trusted
public and hidden checks, grades a separate deterministic rubric, and stores a
versioned evaluation report. A failed evaluation can never enter
`AWAITING_APPROVAL`. Five private answer patches pass; five plausible incomplete
patches and five unchanged submissions fail; repeated evaluation produces the
same verdict hash. See the [evaluation architecture](docs/architecture/evaluation.md)
and [Milestone 5 verification](docs/verification/milestone-5.md).

Milestone 6 now has a patch-bound human approval contract, a review API and
interface, durable publication jobs with leases and delayed retries, and a
GitHub App publisher. A server-side catalog lists authorized installations and
repositories, resolves a selected branch to one exact commit, verifies its Git
tree and blobs, and captures a content-addressed source snapshot before a run
exists. The publisher replays the approved artifact, rechecks the base commit
before any write, creates a deterministic branch and commit, and recovers an
existing pull request after ambiguous retries. Local contract tests prove stale
approvals cannot publish and retries create at most one pull request. Reviewer
identity now comes from a verified Clerk session and an explicit server-side
allowlist; the browser cannot choose the approval actor. Live Clerk and GitHub
proofs plus public PostgreSQL CI evidence remain before the milestone is
complete. See the
[approval and publishing architecture](docs/architecture/approval-publishing.md)
and [Milestone 6 progress record](docs/verification/milestone-6-progress.md).

Milestone 7 has started with a strict benchmark evidence pipeline. It records
one versioned result per task, prevents mixed configurations or silently
dropped invalidations, derives outcome and usage metrics from the durable run
event trail, computes solve-rate uncertainty plus cost/latency/failure metrics,
emits content-addressed JSON and Markdown cards, and provides a source-backed
failure-analysis dashboard with category/split filters and reproduction
digests. The private `forge-private-v1` task set now contains 20 independently
validated tasks: four per planned category and ten holdouts. A frozen
ten-task development baseline and two machine-checked, one-variable experiment
contracts now pin the code, tasks, model configuration, budgets, sandbox, and
pricing snapshot. This is benchmark and experiment-construction evidence, not
model performance; live executions and tagged reproduction remain. See the
[benchmark-reporting architecture](docs/architecture/benchmark-reporting.md),
the [controlled-experiment architecture](docs/architecture/controlled-experiments.md),
and [Milestone 7 progress record](docs/verification/milestone-7-progress.md).

The zero-cost product tour now runs the real local persistence, sandbox,
repository-intelligence, bounded-tool, artifact, and independent-evaluation
path with a deterministic offline runtime. Its human review is persisted in the
backend audit trail, bound to the exact patch and verdict, restored after a page
refresh, and explicitly cannot authorize a GitHub write. This provides a safe
end-to-end demonstration without an API key, provider request, or usage charge.
Real HTTP regression tests now restart an isolated API process and verify
durable approve/reject decisions, idempotent replay, stale-evidence rejection,
and fail-closed review when a persisted patch is missing or altered. See the
[offline HTTP verification record](docs/verification/offline-http.md).

An opt-in controlled API profile now gates every run-data and command route on
an authorized reviewer session, disables local-path/demo execution and API docs,
and checks for PostgreSQL, digest-pinned Docker execution, explicit artifact
storage, and one exact HTTPS web origin before opening dependencies. The local
development defaults are unchanged. This backend safeguard is not deployment
completion: live integration checks remain. The web console now sends reviewer
sessions consistently and uses authenticated, cursor-replaying fetch streams;
session rejection/sign-out clears its in-memory evidence. The trusted offline
demo remains unchanged. See the [controlled API guide](docs/operations/controlled-api.md)
and [authenticated web verification](docs/verification/authenticated-web.md).

## Product boundary

Version 0.1 is intentionally narrow:

- GitHub repositories from a controlled allowlist.
- TypeScript and Python fixtures with documented setup.
- Bounded bug fixes and small features.
- Repository inspection, safe command execution, patching, verification, independent evaluation, diff review, and human-approved pull-request creation.
- Explicit time, command, token, and cost budgets.

Version 0.1 does not include automatic merging, deployment, unrestricted network access, arbitrary repositories, multi-repository work, or Kubernetes.

## Planned repository map

```text
apps/web                    Next.js product interface
services/api               HTTP and server-sent event boundary
services/worker            Durable workflow state machine
services/sandbox-controller Isolated execution boundary
services/publisher         Approval-bound GitHub write boundary
packages/contracts         Versioned cross-service schemas
packages/agent-core        Provider-neutral agent domain
packages/repo-intelligence Repository manifest and retrieval
packages/evaluation        Deterministic and rubric grading
packages/telemetry         Trace and metric conventions
db                         Migrations and seeds
evals                      Public smoke tasks and reports
infra                      Local composition, images, and policies
docs                       Product, architecture, security, and ADRs
```

## Development stages

1. Product contract and test bed.
2. End-to-end walking skeleton.
3. Repository intelligence.
4. Secure execution and patching.
5. Bounded autonomous loop.
6. Independent evaluation.
7. GitHub approval and publishing.
8. Benchmark hardening and public launch.

See [the product contract](docs/product-contract.md), [architecture decisions](docs/adr/README.md), and [threat model](docs/security/threat-model.md).

## Validate the scaffold

```bash
node scripts/validate-scaffold.mjs
```

The command uses only the Node.js standard library and verifies the Milestone 0 repository invariants.

## Run the walking skeleton

Install the workspaces:

```bash
pnpm install
python -m venv .venv
.venv/Scripts/python -m pip install -e packages/agent-core -e packages/repo-intelligence -e packages/evaluation -e services/api -e services/sandbox-controller -e services/worker -e services/publisher
```

Start PostgreSQL and configure Forge:

```bash
docker compose -f infra/compose/compose.yaml up -d postgres
```

On PowerShell:

```powershell
$env:FORGE_DATABASE_URL="postgresql://forge:forge@localhost:5432/forge"
```

If `FORGE_DATABASE_URL` is absent, Forge falls back to the SQLite path in
`FORGE_DATABASE_PATH`.

Start the API:

```bash
.venv/Scripts/python -m uvicorn forge_api.main:app --host 127.0.0.1 --port 8000
```

Start the web console in another terminal:

```bash
pnpm --filter @forge/web dev
```

### Free offline product demo

Open `http://127.0.0.1:3000/demo` to execute a deterministic local run through
the real Forge API, durable event store, sandbox, tool loop, patch artifact,
and independent evaluator. It needs no API key, makes no model or external
network calls, spends no money, and cannot write to GitHub. Its fixed retired
public smoke fixture demonstrates the engineering workflow only; it is not
benchmark or model-quality evidence.

The [offline verification guide](docs/operations/offline-verification.md)
explains how to check review persistence and run the isolated HTTP restart
tests without touching your normal demo database or requiring an API key.

After creating a run, advance one queued run from a third terminal:

```bash
.venv/Scripts/python -m forge_worker.main --database .state/forge.db --once
```

Run the current verification suite:

```bash
python scripts/run-python-tests.py
pnpm web:typecheck
pnpm web:test
pnpm web:build
```

The default walking-skeleton worker path still executes only the fixed
`node --version` command for regression coverage. Start the worker with
`--agent` to use the bounded model/tool path after configuring a model, dated
price inputs, provider credentials, and a durable idempotency ledger.

## License

MIT. See [LICENSE](LICENSE).
