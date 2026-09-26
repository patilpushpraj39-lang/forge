# Forge

Forge is a sandboxed autonomous software-engineering platform. It accepts a bounded task against an immutable repository snapshot, investigates the codebase, proposes and applies a patch inside an isolated environment, verifies the result, evaluates it independently, and asks a human to approve the exact patch before any GitHub write.

This repository is the public product monorepo. Hidden benchmark tests and answer patches are maintained separately so released evaluation results are not contaminated by test leakage.

## Current status

Milestones 0 and 1 are complete. Milestone 2 repository intelligence is
implemented and awaiting clean CI reproduction. Milestone 3 secure execution
is implemented and awaiting its first public Docker abuse-test run. Milestone
4's bounded-agent foundation is active, Milestone 5 independent evaluation
is complete locally, and the Milestone 6 approval/publishing foundation is
implemented. The product contract, architecture decisions,
threat model, repository structure, initial contracts, CI scaffold, three
benchmark fixtures, five seed tasks, and the executable walking skeleton have
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
explicit CPU, memory, process, filesystem, output, and time limits is ready for
its first GitHub abuse-test run. Content-addressed artifacts can use a local
store or an S3-compatible encrypted bucket with explicit retention. See the
[sandbox architecture](docs/architecture/sandbox.md) and
[Milestone 3 progress record](docs/verification/milestone-3-progress.md).

The Milestone 4 foundation adds a provider-neutral model runtime, an OpenAI
Responses API adapter, strict serial tool schemas, code-enforced token/cost/time
and action budgets, durable idempotency records, bounded repository and sandbox
tools, retries, and explicit stop reasons. Runs now persist their objective and
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
GitHub App publisher. The publisher replays the approved artifact, rechecks the
base commit before any write, creates a deterministic branch and commit, and
recovers an existing pull request after ambiguous retries. Local contract tests
prove stale approvals cannot publish and retries create at most one pull
request. Live GitHub App proof, authenticated reviewer identity, immutable
GitHub snapshot ingestion, and public PostgreSQL CI evidence remain before the
milestone is complete. See the [approval and publishing architecture](docs/architecture/approval-publishing.md)
and [Milestone 6 progress record](docs/verification/milestone-6-progress.md).

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

After creating a run, advance one queued run from a third terminal:

```bash
.venv/Scripts/python -m forge_worker.main --database .state/forge.db --once
```

Run the current verification suite:

```bash
python scripts/run-python-tests.py
pnpm web:typecheck
pnpm web:build
```

The default walking-skeleton worker path still executes only the fixed
`node --version` command for regression coverage. Start the worker with
`--agent` to use the bounded model/tool path after configuring a model, dated
price inputs, provider credentials, and a durable idempotency ledger.

## License

MIT. See [LICENSE](LICENSE).
