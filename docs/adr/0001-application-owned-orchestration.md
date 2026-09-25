# ADR 0001 Application Owned Orchestration

- Status: accepted
- Date: 2026-09-25

## Context

Forge must demonstrate durable state transitions, bounded retries, patch-bound approvals, cost controls, crash recovery, and independent evaluation. Delegating the full workflow to a managed agent runtime would shorten implementation but hide several of the project's central engineering claims.

## Decision

Forge will own its workflow state machine and persist every transition. A model runtime performs bounded reasoning steps and tool selection; it does not own run truth, approval, publishing, or recovery.

## Alternatives

- Use a managed agent runtime for the complete workflow.
- Use an in-memory loop and add durability later.
- Adopt a general workflow engine immediately.

## Consequences

Forge must implement leases, idempotency, events, budgets, and recovery. The resulting behavior is more work to build but can be tested, replayed, measured, and explained directly. A managed runtime remains a future adapter and benchmark comparison.

