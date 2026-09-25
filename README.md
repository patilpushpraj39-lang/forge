# Forge

Forge is a sandboxed autonomous software-engineering platform. It accepts a bounded task against an immutable repository snapshot, investigates the codebase, proposes and applies a patch inside an isolated environment, verifies the result, evaluates it independently, and asks a human to approve the exact patch before any GitHub write.

This repository is the public product monorepo. Hidden benchmark tests and answer patches are maintained separately so released evaluation results are not contaminated by test leakage.

## Current status

Milestone 0 is complete. The product contract, architecture decisions, threat model, repository structure, initial contracts, CI scaffold, three benchmark fixtures, and five seed tasks have been validated from clean checkouts. The first executable walking skeleton is the active milestone.

See [the Milestone 0 verification record](docs/verification/milestone-0.md).

Milestone 1 currently has a tested local persistence adapter, leased worker
recovery, bounded active-command cancellation, an SSE API, and a
production-building Next.js console that restores run history after reload. The
worker accesses disposable execution only through an opaque sandbox-controller
protocol. The first controller backend is local and is not a security sandbox.
The local persistence adapter uses SQLite to exercise recovery and replay;
PostgreSQL remains
the required durable backend before the milestone closes. See [the
walking-skeleton design](docs/architecture/walking-skeleton.md).

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
.venv/Scripts/python -m pip install -e packages/agent-core -e services/api -e services/sandbox-controller -e services/worker
```

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

The worker currently creates a disposable copied workspace and executes only the fixed `node --version` command. It is a walking-skeleton boundary, not a security sandbox.

## License

MIT. See [LICENSE](LICENSE).
