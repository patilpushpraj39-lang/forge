from __future__ import annotations

import os
from pathlib import Path

from .run_store import RunStore
from .store_protocol import RunStoreProtocol


def create_run_store(
    *,
    database_url: str | None = None,
    sqlite_path: str | Path | None = None,
    migrations_path: str | Path | None = None,
) -> RunStoreProtocol:
    resolved_url = database_url or os.environ.get("FORGE_DATABASE_URL")
    if resolved_url:
        from .postgres_run_store import PostgresRunStore

        migration_directory = Path(
            migrations_path
            or os.environ.get("FORGE_MIGRATIONS_PATH", "db/migrations")
        )
        return PostgresRunStore(resolved_url, migration_directory)

    resolved_path = sqlite_path or os.environ.get(
        "FORGE_DATABASE_PATH", ".state/forge.db"
    )
    return RunStore(resolved_path)
