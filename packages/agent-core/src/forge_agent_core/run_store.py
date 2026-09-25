from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterator


class RunState(StrEnum):
    CREATED = "CREATED"
    SNAPSHOTTING = "SNAPSHOTTING"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


ACTIVE_STATES = {RunState.SNAPSHOTTING, RunState.EXECUTING}
TERMINAL_STATES = {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}


class RunNotFoundError(KeyError):
    pass


class InvalidTransitionError(RuntimeError):
    pass


class LeaseOwnershipError(RuntimeError):
    pass


def now_utc() -> datetime:
    return datetime.now(UTC)


def now_iso() -> str:
    return now_utc().isoformat()


def expiry_iso(lease_seconds: float) -> str:
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")
    return (now_utc() + timedelta(seconds=lease_seconds)).isoformat()


class RunStore:
    """Milestone 1 local durable store.

    SQLite is a development adapter used to exercise persistence, leases,
    cancellation, and replay while the PostgreSQL adapter is introduced. It is
    not the production source of truth.
    """

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    repository_path TEXT NOT NULL,
                    state TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    cancellation_requested_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS run_events (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    sequence INTEGER NOT NULL,
                    schema_version INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE(run_id, sequence)
                );
                """
            )
            self._migrate_run_columns(connection)
            connection.commit()

    @staticmethod
    def _migrate_run_columns(connection: sqlite3.Connection) -> None:
        existing = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(runs)").fetchall()
        }
        additions = {
            "attempt": "INTEGER NOT NULL DEFAULT 0",
            "lease_owner": "TEXT",
            "lease_expires_at": "TEXT",
            "cancellation_requested_at": "TEXT",
        }
        for column, definition in additions.items():
            if column not in existing:
                connection.execute(
                    f"ALTER TABLE runs ADD COLUMN {column} {definition}"
                )

    def create_run(self, repository_path: str) -> dict[str, Any]:
        run_id = str(uuid.uuid4())
        created_at = now_iso()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO runs(
                    run_id, repository_path, state, attempt, created_at, updated_at
                ) VALUES (?, ?, ?, 0, ?, ?)
                """,
                (run_id, repository_path, RunState.CREATED, created_at, created_at),
            )
            self._append_event(
                connection,
                run_id,
                "run_created",
                "api",
                {"repository_path": repository_path},
            )
            connection.commit()
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise RunNotFoundError(run_id)
        return dict(row)

    def list_events(
        self, run_id: str, after_sequence: int = 0
    ) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM run_events
                WHERE run_id = ? AND sequence > ?
                ORDER BY sequence ASC
                """,
                (run_id, after_sequence),
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def append_event(
        self, run_id: str, event_type: str, actor: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_run(connection, run_id)
            event = self._append_event(
                connection, run_id, event_type, actor, payload
            )
            connection.commit()
        return event

    def transition(
        self,
        run_id: str,
        expected: RunState,
        target: RunState,
        actor: str,
        payload: dict[str, Any] | None = None,
        lease_owner: str | None = None,
    ) -> dict[str, Any]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._require_run(connection, run_id)
            current = RunState(row["state"])
            if current != expected:
                raise InvalidTransitionError(
                    f"expected {expected}, found {current} for run {run_id}"
                )
            if lease_owner is not None:
                self._require_lease_owner(row, lease_owner)

            updated_at = now_iso()
            clear_lease = target in TERMINAL_STATES
            connection.execute(
                """
                UPDATE runs
                SET state = ?, updated_at = ?,
                    lease_owner = CASE WHEN ? THEN NULL ELSE lease_owner END,
                    lease_expires_at = CASE WHEN ? THEN NULL ELSE lease_expires_at END
                WHERE run_id = ?
                """,
                (target, updated_at, clear_lease, clear_lease, run_id),
            )
            self._append_event(
                connection,
                run_id,
                "state_changed",
                actor,
                {"from_state": current, "to_state": target, **(payload or {})},
            )
            connection.commit()
        return self.get_run(run_id)

    def claim_next_run(
        self, worker_id: str, lease_seconds: float = 30
    ) -> dict[str, Any] | None:
        expires_at = expiry_iso(lease_seconds)
        claimed_at = now_iso()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._finalize_abandoned_cancellations(connection, claimed_at)
            row = connection.execute(
                """
                SELECT * FROM runs
                WHERE cancellation_requested_at IS NULL
                  AND (
                    state = ?
                    OR (
                      state IN (?, ?)
                      AND lease_expires_at IS NOT NULL
                      AND lease_expires_at <= ?
                    )
                  )
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (
                    RunState.CREATED,
                    RunState.SNAPSHOTTING,
                    RunState.EXECUTING,
                    claimed_at,
                ),
            ).fetchone()
            if row is None:
                connection.commit()
                return None

            run_id = str(row["run_id"])
            previous_state = RunState(row["state"])
            previous_owner = row["lease_owner"]
            attempt = int(row["attempt"]) + 1
            connection.execute(
                """
                UPDATE runs
                SET state = ?, attempt = ?, lease_owner = ?, lease_expires_at = ?,
                    updated_at = ?
                WHERE run_id = ?
                """,
                (
                    RunState.SNAPSHOTTING,
                    attempt,
                    worker_id,
                    expires_at,
                    claimed_at,
                    run_id,
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
            self._append_event(connection, run_id, event_type, "worker", payload)
            connection.commit()
        return self.get_run(run_id)

    def renew_lease(
        self, run_id: str, worker_id: str, lease_seconds: float = 30
    ) -> dict[str, Any]:
        expires_at = expiry_iso(lease_seconds)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._require_run(connection, run_id)
            self._require_lease_owner(row, worker_id)
            if RunState(row["state"]) not in ACTIVE_STATES:
                raise InvalidTransitionError("cannot renew a lease for an inactive run")
            connection.execute(
                """
                UPDATE runs
                SET lease_expires_at = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (expires_at, now_iso(), run_id),
            )
            connection.commit()
        return self.get_run(run_id)

    def request_cancellation(self, run_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._require_run(connection, run_id)
            current = RunState(row["state"])
            if current in TERMINAL_STATES:
                connection.commit()
                return dict(row)

            requested_at = str(row["cancellation_requested_at"] or now_iso())
            if row["cancellation_requested_at"] is None:
                connection.execute(
                    """
                    UPDATE runs
                    SET cancellation_requested_at = ?, updated_at = ?
                    WHERE run_id = ?
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

            if current == RunState.CREATED:
                connection.execute(
                    """
                    UPDATE runs
                    SET state = ?, lease_owner = NULL, lease_expires_at = NULL,
                        updated_at = ?
                    WHERE run_id = ?
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
            connection.commit()
        return self.get_run(run_id)

    def is_cancellation_requested(self, run_id: str) -> bool:
        return self.get_run(run_id)["cancellation_requested_at"] is not None

    def acknowledge_cancellation(
        self, run_id: str, worker_id: str
    ) -> dict[str, Any]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._require_run(connection, run_id)
            current = RunState(row["state"])
            if current == RunState.CANCELLED:
                connection.commit()
                return dict(row)
            self._require_lease_owner(row, worker_id)
            if row["cancellation_requested_at"] is None:
                raise InvalidTransitionError("cancellation was not requested")
            if current not in ACTIVE_STATES:
                raise InvalidTransitionError("cannot cancel an inactive run")

            connection.execute(
                """
                UPDATE runs
                SET state = ?, lease_owner = NULL, lease_expires_at = NULL,
                    updated_at = ?
                WHERE run_id = ?
                """,
                (RunState.CANCELLED, now_iso(), run_id),
            )
            self._append_event(
                connection,
                run_id,
                "state_changed",
                "worker",
                {"from_state": current, "to_state": RunState.CANCELLED},
            )
            connection.commit()
        return self.get_run(run_id)

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        """Compatibility alias for the API boundary."""
        return self.request_cancellation(run_id)

    def _finalize_abandoned_cancellations(
        self, connection: sqlite3.Connection, observed_at: str
    ) -> None:
        rows = connection.execute(
            """
            SELECT * FROM runs
            WHERE state IN (?, ?)
              AND cancellation_requested_at IS NOT NULL
              AND (lease_expires_at IS NULL OR lease_expires_at <= ?)
            """,
            (RunState.SNAPSHOTTING, RunState.EXECUTING, observed_at),
        ).fetchall()
        for row in rows:
            run_id = str(row["run_id"])
            current = RunState(row["state"])
            connection.execute(
                """
                UPDATE runs
                SET state = ?, lease_owner = NULL, lease_expires_at = NULL,
                    updated_at = ?
                WHERE run_id = ?
                """,
                (RunState.CANCELLED, observed_at, run_id),
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
    def _require_lease_owner(row: sqlite3.Row, worker_id: str) -> None:
        if row["lease_owner"] != worker_id:
            raise LeaseOwnershipError(
                f"worker {worker_id} does not own run {row['run_id']}"
            )

    def _require_run(
        self, connection: sqlite3.Connection, run_id: str
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise RunNotFoundError(run_id)
        return row

    def _append_event(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        event_type: str,
        actor: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        sequence = int(
            connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM run_events WHERE run_id = ?",
                (run_id,),
            ).fetchone()[0]
        )
        event = {
            "event_id": str(uuid.uuid4()),
            "run_id": run_id,
            "sequence": sequence,
            "schema_version": 1,
            "event_type": event_type,
            "occurred_at": now_iso(),
            "actor": actor,
            "payload": payload,
        }
        connection.execute(
            """
            INSERT INTO run_events(
                event_id, run_id, sequence, schema_version, event_type,
                occurred_at, actor, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["event_id"],
                run_id,
                sequence,
                1,
                event_type,
                event["occurred_at"],
                actor,
                json.dumps(payload, separators=(",", ":")),
            ),
        )
        return event

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> dict[str, Any]:
        event = dict(row)
        event["payload"] = json.loads(event.pop("payload_json"))
        return event

