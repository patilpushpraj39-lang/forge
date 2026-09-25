from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
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


TERMINAL_STATES = {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}


class RunNotFoundError(KeyError):
    pass


class InvalidTransitionError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


class RunStore:
    """Milestone 1 local durable store.

    SQLite is a development adapter used to exercise persistence and replay while
    the PostgreSQL schema is introduced. It is not the production source of truth.
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

    def create_run(self, repository_path: str) -> dict[str, Any]:
        run_id = str(uuid.uuid4())
        created_at = now_iso()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO runs(run_id, repository_path, state, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
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
    ) -> dict[str, Any]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._require_run(connection, run_id)
            current = RunState(row["state"])
            if current != expected:
                raise InvalidTransitionError(
                    f"expected {expected}, found {current} for run {run_id}"
                )
            updated_at = now_iso()
            connection.execute(
                "UPDATE runs SET state = ?, updated_at = ? WHERE run_id = ?",
                (target, updated_at, run_id),
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

    def claim_next_created_run(self) -> dict[str, Any] | None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM runs
                WHERE state = ?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (RunState.CREATED,),
            ).fetchone()
            if row is None:
                connection.rollback()
                return None
            run_id = str(row["run_id"])
            updated_at = now_iso()
            connection.execute(
                "UPDATE runs SET state = ?, updated_at = ? WHERE run_id = ?",
                (RunState.SNAPSHOTTING, updated_at, run_id),
            )
            self._append_event(
                connection,
                run_id,
                "state_changed",
                "worker",
                {"from_state": RunState.CREATED, "to_state": RunState.SNAPSHOTTING},
            )
            connection.commit()
        return self.get_run(run_id)

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._require_run(connection, run_id)
            current = RunState(row["state"])
            if current in TERMINAL_STATES:
                connection.rollback()
                return self.get_run(run_id)
            updated_at = now_iso()
            connection.execute(
                "UPDATE runs SET state = ?, updated_at = ? WHERE run_id = ?",
                (RunState.CANCELLED, updated_at, run_id),
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

