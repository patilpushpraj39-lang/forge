# Forge Database

PostgreSQL is the durable source of truth for runs, transitions, leases,
approvals, outbox records, evaluation metadata, model usage, and artifact
references.

Migrations are immutable, ordered SQL files in `db/migrations`. Forge records
their SHA-256 checksums in `_forge_schema_migrations`, rejects changed applied
migrations, and serializes concurrent migration attempts with an advisory lock.

The first migration includes materialized run state, append-only events, and a
transactional outbox. Worker claims use `for update skip locked`; partial
indexes cover new runs, expired leases, abandoned cancellations, and unpublished
outbox messages.
