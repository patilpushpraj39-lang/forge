# Forge

Forge is a sandboxed autonomous software-engineering platform. It accepts a bounded task against an immutable repository snapshot, investigates the codebase, proposes and applies a patch inside an isolated environment, verifies the result, evaluates it independently, and asks a human to approve the exact patch before any GitHub write.

This repository is the public product monorepo. Hidden benchmark tests and answer patches are maintained separately so released evaluation results are not contaminated by test leakage.

## Current status

Milestone 0 is in progress. The product contract, architecture decisions, threat model, repository structure, initial contracts, and CI scaffold are present. The first executable walking skeleton is the next milestone.

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

## License

MIT. See [LICENSE](LICENSE).

