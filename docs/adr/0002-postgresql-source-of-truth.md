# ADR 0002 PostgreSQL Durable Source of Truth

- Status: accepted
- Date: 2026-09-25

## Context

Run state, leases, approvals, outbox records, and benchmark metadata require transactions and strong consistency. A queue or cache alone cannot safely authorize external writes.

## Decision

PostgreSQL will store materialized workflow state and an append-only run event stream. Large artifacts live in content-addressed object storage. Redis may later provide event fan-out or queue acceleration but is not required for correctness.

## Alternatives

- Redis as the primary state store.
- Pure event sourcing without materialized state.
- A managed workflow service as the first dependency.

## Consequences

Workers can use row versions, leases, unique idempotency keys, and transactional outbox records. The system must manage migrations and event-schema compatibility. Artifact integrity depends on stored hashes and retention policy.

