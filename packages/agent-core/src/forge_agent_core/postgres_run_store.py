from __future__ import annotations

import hashlib
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

import psycopg
from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .run_store import (
    ACTIVE_STATES,
    TERMINAL_STATES,
    InvalidTransitionError,
    LEASE_RELEASE_STATES,
    LeaseOwnershipError,
    RunNotFoundError,
    RunState,
)
from .model_runtime import BudgetLimits
from .run_contract import (
    DEFAULT_RUN_BUDGETS,
    DEFAULT_RUN_OBJECTIVE,
    normalize_objective,
    run_budget_payload,
    validate_run_budgets,
)


class MigrationChecksumError(RuntimeError):
    pass


def _migration_checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def apply_migrations(database_url: str, migrations_path: str | Path) -> None:
    directory = Path(migrations_path)
    migration_files = sorted(directory.glob("*.sql"))
    if not migration_files:
        raise FileNotFoundError(f"no PostgreSQL migrations found in {directory}")

    with psycopg.connect(database_url, autocommit=True, row_factory=dict_row) as db:
        db.execute(
            """
            create table if not exists _forge_schema_migrations (
                version text primary key,
                checksum text not null,
                applied_at timestamptz not null default clock_timestamp()
            )
            """
        )
        for migration in migration_files:
            version = migration.name
            checksum = _migration_checksum(migration)
            with db.transaction():
                db.execute(
                    "select pg_advisory_xact_lock(hashtext(%s))",
                    ("forge-schema-migrations",),
                )
                applied = db.execute(
                    """
                    select checksum
                    from _forge_schema_migrations
                    where version = %s
                    """,
                    (version,),
                ).fetchone()
                if applied is not None:
                    if applied["checksum"] != checksum:
                        raise MigrationChecksumError(
                            f"migration {version} changed after it was applied"
                        )
                    continue
                db.execute(migration.read_text(encoding="utf-8"))
                db.execute(
                    """
                    insert into _forge_schema_migrations(version, checksum)
                    values (%s, %s)
                    """,
                    (version, checksum),
                )


class PostgresRunStore:
    """PostgreSQL source of truth for Forge run state and append-only events."""

    def __init__(self, database_url: str, migrations_path: str | Path) -> None:
        self.database_url = database_url
        self.migrations_path = Path(migrations_path)
        apply_migrations(database_url, self.migrations_path)
        self._pool = ConnectionPool(
            database_url,
            min_size=0,
            max_size=10,
            kwargs={"autocommit": True, "row_factory": dict_row},
            open=True,
        )

    @contextmanager
    def _connection(self) -> Iterator[psycopg.Connection[dict[str, Any]]]:
        with self._pool.connection() as connection:
            yield connection

    def close(self) -> None:
        self._pool.close()

    def create_run(
        self,
        repository_path: str,
        objective: str = DEFAULT_RUN_OBJECTIVE,
        budgets: BudgetLimits | None = None,
    ) -> dict[str, Any]:
        objective = normalize_objective(objective)
        budgets = validate_run_budgets(budgets or DEFAULT_RUN_BUDGETS)
        run_id = str(uuid.uuid4())
        with self._connection() as connection, connection.transaction():
            connection.execute(
                """
                insert into runs(
                    run_id,
                    repository_path,
                    objective,
                    budget_total_tokens,
                    budget_cost_microusd,
                    budget_wall_seconds,
                    budget_model_steps,
                    budget_tool_calls,
                    budget_patch_attempts,
                    state
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    run_id,
                    repository_path,
                    objective,
                    budgets.total_tokens,
                    budgets.cost_microusd,
                    budgets.wall_seconds,
                    budgets.model_steps,
                    budgets.tool_calls,
                    budgets.patch_attempts,
                    RunState.CREATED,
                ),
            )
            self._append_event(
                connection,
                run_id,
                "run_created",
                "api",
                {
                    "repository_path": repository_path,
                    "objective": objective,
                    "budgets": run_budget_payload(budgets),
                },
            )
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                "select * from runs where run_id = %s", (run_id,)
            ).fetchone()
        if row is None:
            raise RunNotFoundError(run_id)
        return self._run_from_row(row)

    def list_events(
        self, run_id: str, after_sequence: int = 0
    ) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                select * from run_events
                where run_id = %s and sequence > %s
                order by sequence asc
                """,
                (run_id, after_sequence),
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def append_event(
        self,
        run_id: str,
        event_type: str,
        actor: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        with self._connection() as connection, connection.transaction():
            self._require_run(connection, run_id, for_update=True)
            return self._append_event(
                connection, run_id, event_type, actor, payload
            )

    def transition(
        self,
        run_id: str,
        expected: RunState,
        target: RunState,
        actor: str,
        payload: dict[str, Any] | None = None,
        lease_owner: str | None = None,
    ) -> dict[str, Any]:
        with self._connection() as connection, connection.transaction():
            row = self._require_run(connection, run_id, for_update=True)
            current = RunState(row["state"])
            if current != expected:
                raise InvalidTransitionError(
                    f"expected {expected}, found {current} for run {run_id}"
                )
            if lease_owner is not None:
                self._require_lease_owner(row, lease_owner)
            clear_lease = target in LEASE_RELEASE_STATES
            connection.execute(
                """
                update runs
                set state = %s,
                    updated_at = clock_timestamp(),
                    row_version = row_version + 1,
                    lease_owner = case when %s then null else lease_owner end,
                    lease_expires_at = case
                        when %s then null else lease_expires_at
                    end
                where run_id = %s
                """,
                (target, clear_lease, clear_lease, run_id),
            )
            self._append_event(
                connection,
                run_id,
                "state_changed",
                actor,
                {"from_state": current, "to_state": target, **(payload or {})},
            )
        return self.get_run(run_id)

    def claim_next_run(
        self, worker_id: str, lease_seconds: float = 30
    ) -> dict[str, Any] | None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        lease_duration = timedelta(seconds=lease_seconds)
        claimed_run_id: str | None = None
        with self._connection() as connection, connection.transaction():
            self._finalize_abandoned_cancellations(connection)
            row = connection.execute(
                """
                select *
                from runs
                where cancellation_requested_at is null
                  and (
                    state = %s
                    or (
                      state in (%s, %s, %s)
                      and lease_expires_at is not null
                      and lease_expires_at <= clock_timestamp()
                    )
                  )
                order by created_at asc, run_id asc
                limit 1
                for update skip locked
                """,
                (
                    RunState.CREATED,
                    RunState.SNAPSHOTTING,
                    RunState.EXECUTING,
                    RunState.EVALUATING,
                ),
            ).fetchone()
            if row is None:
                return None

            claimed_run_id = str(row["run_id"])
            previous_state = RunState(row["state"])
            previous_owner = row["lease_owner"]
            attempt = int(row["attempt"]) + 1
            connection.execute(
                """
                update runs
                set state = %s,
                    attempt = %s,
                    lease_owner = %s,
                    lease_expires_at = clock_timestamp() + %s,
                    updated_at = clock_timestamp(),
                    row_version = row_version + 1
                where run_id = %s
                """,
                (
                    RunState.SNAPSHOTTING,
                    attempt,
                    worker_id,
                    lease_duration,
                    claimed_run_id,
                ),
            )
            if previous_state == RunState.CREATED:
                event_type = "state_changed"
                payload = {
                    "from_state": RunState.CREATED,
                    "to_state": RunState.SNAPSHOTTING,
                    "worker_id": worker_id,
                    "attempt": attempt,
                }
            else:
                event_type = "lease_recovered"
                payload = {
                    "from_state": previous_state,
                    "to_state": RunState.SNAPSHOTTING,
                    "previous_worker_id": previous_owner,
                    "worker_id": worker_id,
                    "attempt": attempt,
                }
            self._append_event(
                connection, claimed_run_id, event_type, "worker", payload
            )
        return self.get_run(claimed_run_id)

    def renew_lease(
        self, run_id: str, worker_id: str, lease_seconds: float = 30
    ) -> dict[str, Any]:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        with self._connection() as connection, connection.transaction():
            row = self._require_run(connection, run_id, for_update=True)
            self._require_lease_owner(row, worker_id)
            if RunState(row["state"]) not in ACTIVE_STATES:
                raise InvalidTransitionError("cannot renew a lease for an inactive run")
            connection.execute(
                """
                update runs
                set lease_expires_at = clock_timestamp() + %s,
                    updated_at = clock_timestamp(),
                    row_version = row_version + 1
                where run_id = %s
                """,
                (timedelta(seconds=lease_seconds), run_id),
            )
        return self.get_run(run_id)

    def request_cancellation(self, run_id: str) -> dict[str, Any]:
        with self._connection() as connection, connection.transaction():
            row = self._require_run(connection, run_id, for_update=True)
            current = RunState(row["state"])
            if current in TERMINAL_STATES:
                return self._run_from_row(row)

            requested_at = row["cancellation_requested_at"] or datetime.now(UTC)
            if row["cancellation_requested_at"] is None:
                connection.execute(
                    """
                    update runs
                    set cancellation_requested_at = %s,
                        updated_at = %s,
                        row_version = row_version + 1
                    where run_id = %s
                    """,
                    (requested_at, requested_at, run_id),
                )
                self._append_event(
                    connection,
                    run_id,
                    "cancellation_requested",
                    "api",
                    {"state": current},
                )

            if current in {RunState.CREATED, RunState.AWAITING_APPROVAL}:
                connection.execute(
                    """
                    update runs
                    set state = %s,
                        lease_owner = null,
                        lease_expires_at = null,
                        updated_at = %s,
                        row_version = row_version + 1
                    where run_id = %s
                    """,
                    (RunState.CANCELLED, requested_at, run_id),
                )
                self._append_event(
                    connection,
                    run_id,
                    "state_changed",
                    "api",
                    {"from_state": current, "to_state": RunState.CANCELLED},
                )
        return self.get_run(run_id)

    def is_cancellation_requested(self, run_id: str) -> bool:
        return self.get_run(run_id)["cancellation_requested_at"] is not None

    def acknowledge_cancellation(
        self, run_id: str, worker_id: str
    ) -> dict[str, Any]:
        with self._connection() as connection, connection.transaction():
            row = self._require_run(connection, run_id, for_update=True)
            current = RunState(row["state"])
            if current == RunState.CANCELLED:
                return self._run_from_row(row)
            self._require_lease_owner(row, worker_id)
            if row["cancellation_requested_at"] is None:
                raise InvalidTransitionError("cancellation was not requested")
            if current not in ACTIVE_STATES:
                raise InvalidTransitionError("cannot cancel an inactive run")
            connection.execute(
                """
                update runs
                set state = %s,
                    lease_owner = null,
                    lease_expires_at = null,
                    updated_at = clock_timestamp(),
                    row_version = row_version + 1
                where run_id = %s
                """,
                (RunState.CANCELLED, run_id),
            )
            self._append_event(
                connection,
                run_id,
                "state_changed",
                "worker",
                {"from_state": current, "to_state": RunState.CANCELLED},
            )
        return self.get_run(run_id)

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        return self.request_cancellation(run_id)

    def _finalize_abandoned_cancellations(
        self, connection: psycopg.Connection[dict[str, Any]]
    ) -> None:
        rows = connection.execute(
            """
            select *
            from runs
            where state in (%s, %s, %s)
              and cancellation_requested_at is not null
              and (
                lease_expires_at is null
                or lease_expires_at <= clock_timestamp()
              )
            order by created_at asc, run_id asc
            limit 100
            for update skip locked
            """,
            (
                RunState.SNAPSHOTTING,
                RunState.EXECUTING,
                RunState.EVALUATING,
            ),
        ).fetchall()
        for row in rows:
            run_id = str(row["run_id"])
            current = RunState(row["state"])
            connection.execute(
                """
                update runs
                set state = %s,
                    lease_owner = null,
                    lease_expires_at = null,
                    updated_at = clock_timestamp(),
                    row_version = row_version + 1
                where run_id = %s
                """,
                (RunState.CANCELLED, run_id),
            )
            self._append_event(
                connection,
                run_id,
                "state_changed",
                "worker",
                {
                    "from_state": current,
                    "to_state": RunState.CANCELLED,
                    "reason": "cancellation_recovered_after_lease_expiry",
                },
            )

    @staticmethod
    def _require_lease_owner(row: dict[str, Any], worker_id: str) -> None:
        if row["lease_owner"] != worker_id:
            raise LeaseOwnershipError(
                f"worker {worker_id} does not own run {row['run_id']}"
            )

    @staticmethod
    def _require_run(
        connection: psycopg.Connection[dict[str, Any]],
        run_id: str,
        *,
        for_update: bool = False,
    ) -> dict[str, Any]:
        suffix = " for update" if for_update else ""
        row = connection.execute(
            f"select * from runs where run_id = %s{suffix}", (run_id,)
        ).fetchone()
        if row is None:
            raise RunNotFoundError(run_id)
        return row

    def _append_event(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        run_id: str,
        event_type: str,
        actor: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        sequence_row = connection.execute(
            """
            select coalesce(max(sequence), 0) + 1 as next_sequence
            from run_events
            where run_id = %s
            """,
            (run_id,),
        ).fetchone()
        assert sequence_row is not None
        event = {
            "event_id": str(uuid.uuid4()),
            "run_id": run_id,
            "sequence": int(sequence_row["next_sequence"]),
            "schema_version": 1,
            "event_type": event_type,
            "occurred_at": datetime.now(UTC).isoformat(),
            "actor": actor,
            "payload": payload,
        }
        connection.execute(
            """
            insert into run_events(
                event_id,
                run_id,
                sequence,
                schema_version,
                event_type,
                occurred_at,
                actor,
                payload
            ) values (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                event["event_id"],
                run_id,
                event["sequence"],
                event["schema_version"],
                event_type,
                event["occurred_at"],
                actor,
                Jsonb(payload),
            ),
        )
        connection.execute(
            """
            insert into outbox_messages(aggregate_id, event_id, topic, payload)
            values (%s, %s, %s, %s)
            """,
            (run_id, event["event_id"], "run.event", Jsonb(event)),
        )
        return event

    @staticmethod
    def _run_from_row(row: dict[str, Any]) -> dict[str, Any]:
        run = dict(row)
        run["run_id"] = str(run["run_id"])
        for key in (
            "lease_expires_at",
            "cancellation_requested_at",
            "created_at",
            "updated_at",
        ):
            if run[key] is not None:
                run[key] = run[key].isoformat()
        return run

    @staticmethod
    def _event_from_row(row: dict[str, Any]) -> dict[str, Any]:
        event = dict(row)
        event["event_id"] = str(event["event_id"])
        event["run_id"] = str(event["run_id"])
        event["occurred_at"] = event["occurred_at"].isoformat()
        return event
