# Local Composition

Start the Milestone 1 PostgreSQL dependency from the repository root:

```bash
docker compose -f infra/compose/compose.yaml up -d postgres
```

Use `postgresql://forge:forge@localhost:5432/forge` as
`FORGE_DATABASE_URL`. Object storage and the container-backed sandbox arrive in
later milestones; Kubernetes is explicitly deferred.
