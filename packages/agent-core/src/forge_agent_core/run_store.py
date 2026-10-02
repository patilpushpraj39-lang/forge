from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterator

from .model_runtime import BudgetLimits
from .publication_contract import (
    ApprovalAction,
    ApprovalError,
    ApprovalEvidenceMismatchError,
    ApprovalExpiredError,
    PublicationConflictError,
    RepositoryTarget,
    validate_actor_id,
    validate_approval_ttl,
    validate_idempotency_key,
    validate_git_commit_sha,
    validate_publication_result,
    validate_pull_request_text,
    validate_sha256,
)
from .run_contract import (
    DEFAULT_RUN_BUDGETS,
    DEFAULT_RUN_OBJECTIVE,
    normalize_objective,
    run_budget_payload,
    validate_run_budgets,
)
from .source_contract import SourceSnapshot


class RunState(StrEnum):
    CREATED = "CREATED"
    SNAPSHOTTING = "SNAPSHOTTING"
    EXECUTING = "EXECUTING"
    EVALUATING = "EVALUATING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    PUBLISHING = "PUBLISHING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


ACTIVE_STATES = {
    RunState.SNAPSHOTTING,
    RunState.EXECUTING,
    RunState.EVALUATING,
}
TERMINAL_STATES = {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}
LEASE_RELEASE_STATES = TERMINAL_STATES | {
    RunState.AWAITING_APPROVAL,
    RunState.PUBLISHING,
}


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


def publication_retry_delay_seconds(attempt: int) -> int:
    """Return a bounded exponential delay for durable publication retries."""
    exponent = max(0, min(attempt - 1, 6))
    return min(300, 5 * (2**exponent))


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
                    objective TEXT NOT NULL,
                    budget_total_tokens INTEGER NOT NULL,
                    budget_cost_microusd INTEGER NOT NULL,
                    budget_wall_seconds REAL NOT NULL,
                    budget_model_steps INTEGER NOT NULL,
                    budget_tool_calls INTEGER NOT NULL,
                    budget_patch_attempts INTEGER NOT NULL,
                    repository_owner TEXT,
                    repository_name TEXT,
                    installation_id INTEGER,
                    base_ref TEXT,
                    base_sha TEXT,
                    evaluated_patch_hash TEXT,
                    evaluation_verdict_hash TEXT,
                    source_snapshot_sha256 TEXT,
                    source_snapshot_size_bytes INTEGER,
                    source_snapshot_media_type TEXT,
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

                CREATE TABLE IF NOT EXISTS approvals (
                    approval_id TEXT PRIMARY KEY,
                    approval_key TEXT NOT NULL UNIQUE,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    patch_hash TEXT NOT NULL,
                    evaluation_verdict_hash TEXT NOT NULL,
                    repository_owner TEXT NOT NULL,
                    repository_name TEXT NOT NULL,
                    installation_id INTEGER NOT NULL,
                    base_ref TEXT NOT NULL,
                    base_sha TEXT NOT NULL,
                    action TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS approvals_run_created_idx
                    ON approvals(run_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS publication_jobs (
                    publication_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    run_id TEXT NOT NULL UNIQUE REFERENCES runs(run_id),
                    approval_id TEXT NOT NULL REFERENCES approvals(approval_id),
                    patch_hash TEXT NOT NULL,
                    repository_owner TEXT NOT NULL,
                    repository_name TEXT NOT NULL,
                    installation_id INTEGER NOT NULL,
                    base_ref TEXT NOT NULL,
                    base_sha TEXT NOT NULL,
                    branch_name TEXT NOT NULL,
                    pull_request_title TEXT NOT NULL,
                    pull_request_body TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    available_at TEXT NOT NULL,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    github_pull_request_number INTEGER,
                    github_pull_request_url TEXT,
                    github_head_sha TEXT,
                    failure_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS publication_jobs_claim_idx
                    ON publication_jobs(status, available_at, lease_expires_at, created_at);
                """
            )
            self._migrate_run_columns(connection)
            self._migrate_publication_columns(connection)
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
            "objective": (
                "TEXT NOT NULL DEFAULT "
                "'Inspect the repository and report verified findings.'"
            ),
            "budget_total_tokens": "INTEGER NOT NULL DEFAULT 50000",
            "budget_cost_microusd": "INTEGER NOT NULL DEFAULT 1000000",
            "budget_wall_seconds": "REAL NOT NULL DEFAULT 600",
            "budget_model_steps": "INTEGER NOT NULL DEFAULT 30",
            "budget_tool_calls": "INTEGER NOT NULL DEFAULT 80",
            "budget_patch_attempts": "INTEGER NOT NULL DEFAULT 5",
            "repository_owner": "TEXT",
            "repository_name": "TEXT",
            "installation_id": "INTEGER",
            "base_ref": "TEXT",
            "base_sha": "TEXT",
            "evaluated_patch_hash": "TEXT",
            "evaluation_verdict_hash": "TEXT",
            "source_snapshot_sha256": "TEXT",
            "source_snapshot_size_bytes": "INTEGER",
            "source_snapshot_media_type": "TEXT",
        }
        for column, definition in additions.items():
            if column not in existing:
                connection.execute(
                    f"ALTER TABLE runs ADD COLUMN {column} {definition}"
                )

    @staticmethod
    def _migrate_publication_columns(connection: sqlite3.Connection) -> None:
        existing = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(publication_jobs)"
            ).fetchall()
        }
        if "available_at" not in existing:
            connection.execute(
                "ALTER TABLE publication_jobs ADD COLUMN available_at TEXT"
            )
            connection.execute(
                """
                UPDATE publication_jobs
                SET available_at = created_at
                WHERE available_at IS NULL
                """
            )

    def create_run(
        self,
        repository_path: str,
        objective: str = DEFAULT_RUN_OBJECTIVE,
        budgets: BudgetLimits | None = None,
        repository: RepositoryTarget | None = None,
        source_snapshot: SourceSnapshot | None = None,
    ) -> dict[str, Any]:
        objective = normalize_objective(objective)
        budgets = validate_run_budgets(budgets or DEFAULT_RUN_BUDGETS)
        run_id = str(uuid.uuid4())
        created_at = now_iso()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO runs(
                    run_id, repository_path, objective,
                    budget_total_tokens, budget_cost_microusd,
                    budget_wall_seconds, budget_model_steps,
                    budget_tool_calls, budget_patch_attempts,
                    repository_owner, repository_name, installation_id,
                    base_ref, base_sha,
                    source_snapshot_sha256, source_snapshot_size_bytes,
                    source_snapshot_media_type,
                    state, attempt, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    0, ?, ?
                )
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
                    repository.owner if repository is not None else None,
                    repository.name if repository is not None else None,
                    repository.installation_id if repository is not None else None,
                    repository.base_ref if repository is not None else None,
                    repository.base_sha if repository is not None else None,
                    source_snapshot.sha256 if source_snapshot is not None else None,
                    (
                        source_snapshot.size_bytes
                        if source_snapshot is not None
                        else None
                    ),
                    (
                        source_snapshot.media_type
                        if source_snapshot is not None
                        else None
                    ),
                    RunState.CREATED,
                    created_at,
                    created_at,
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
                    "repository": (
                        repository.to_dict() if repository is not None else None
                    ),
                    "source_snapshot": (
                        source_snapshot.to_dict()
                        if source_snapshot is not None
                        else None
                    ),
                },
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
            if lease_owner is not None:
                self._require_lease_owner(row, lease_owner)
            current = RunState(row["state"])
            if current != expected:
                raise InvalidTransitionError(
                    f"expected {expected}, found {current} for run {run_id}"
                )
            updated_at = now_iso()
            clear_lease = target in LEASE_RELEASE_STATES
            evaluation_hashes: tuple[str, str] | None = None
            if target == RunState.AWAITING_APPROVAL:
                transition_payload = payload or {}
                evaluation_hashes = (
                    validate_sha256(
                        str(transition_payload.get("diff_hash", "")),
                        "diff_hash",
                    ),
                    validate_sha256(
                        str(transition_payload.get("verdict_hash", "")),
                        "verdict_hash",
                    ),
                )
            if evaluation_hashes is None:
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
            else:
                connection.execute(
                    """
                    UPDATE runs
                    SET state = ?, updated_at = ?,
                        evaluated_patch_hash = ?, evaluation_verdict_hash = ?,
                        lease_owner = NULL, lease_expires_at = NULL
                    WHERE run_id = ?
                    """,
                    (target, updated_at, *evaluation_hashes, run_id),
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
                      state IN (?, ?, ?)
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
                    RunState.EVALUATING,
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

    def claim_run(
        self, run_id: str, worker_id: str, lease_seconds: float = 30
    ) -> dict[str, Any] | None:
        """Claim one known run without consuming an unrelated queued run."""
        expires_at = expiry_iso(lease_seconds)
        claimed_at = now_iso()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._finalize_abandoned_cancellations(connection, claimed_at)
            row = connection.execute(
                """
                SELECT * FROM runs
                WHERE run_id = ?
                  AND cancellation_requested_at IS NULL
                  AND (
                    state = ?
                    OR (
                      state IN (?, ?, ?)
                      AND lease_expires_at IS NOT NULL
                      AND lease_expires_at <= ?
                    )
                  )
                LIMIT 1
                """,
                (
                    run_id,
                    RunState.CREATED,
                    RunState.SNAPSHOTTING,
                    RunState.EXECUTING,
                    RunState.EVALUATING,
                    claimed_at,
                ),
            ).fetchone()
            if row is None:
                connection.commit()
                return None

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
            if current == RunState.PUBLISHING:
                raise PublicationConflictError(
                    "publication has already started and cannot be cancelled"
                )

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

            if current in {RunState.CREATED, RunState.AWAITING_APPROVAL}:
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

    def grant_approval(
        self,
        run_id: str,
        patch_hash: str,
        evaluation_verdict_hash: str,
        actor_id: str,
        approval_key: str,
        expires_in_seconds: int = 900,
        action: ApprovalAction = ApprovalAction.CREATE_PULL_REQUEST,
    ) -> dict[str, Any]:
        patch_hash = validate_sha256(patch_hash, "patch_hash")
        evaluation_verdict_hash = validate_sha256(
            evaluation_verdict_hash, "evaluation_verdict_hash"
        )
        actor_id = validate_actor_id(actor_id)
        approval_key = validate_idempotency_key(approval_key)
        expires_in_seconds = validate_approval_ttl(expires_in_seconds)
        action = ApprovalAction(action)
        created_at = now_utc()
        expires_at = created_at + timedelta(seconds=expires_in_seconds)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM approvals WHERE approval_key = ?",
                (approval_key,),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["run_id"]) != run_id
                    or existing["patch_hash"] != patch_hash
                    or existing["evaluation_verdict_hash"]
                    != evaluation_verdict_hash
                    or existing["actor_id"] != actor_id
                    or existing["action"] != action
                ):
                    raise ApprovalEvidenceMismatchError(
                        "approval key was already used for different evidence"
                    )
                connection.commit()
                return dict(existing)

            run = self._require_run(connection, run_id)
            if RunState(run["state"]) != RunState.AWAITING_APPROVAL:
                raise ApprovalError("run is not awaiting approval")
            if run["repository_owner"] is None:
                raise ApprovalError("run has no GitHub repository target")
            if (
                run["evaluated_patch_hash"] != patch_hash
                or run["evaluation_verdict_hash"] != evaluation_verdict_hash
            ):
                raise ApprovalEvidenceMismatchError(
                    "approval does not match the evaluated patch and verdict"
                )

            approval_id = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO approvals(
                    approval_id, approval_key, run_id, patch_hash,
                    evaluation_verdict_hash, repository_owner, repository_name,
                    installation_id, base_ref, base_sha, action, actor_id, expires_at,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval_id,
                    approval_key,
                    run_id,
                    patch_hash,
                    evaluation_verdict_hash,
                    run["repository_owner"],
                    run["repository_name"],
                    run["installation_id"],
                    run["base_ref"],
                    run["base_sha"],
                    action,
                    actor_id,
                    expires_at.isoformat(),
                    created_at.isoformat(),
                ),
            )
            self._append_event(
                connection,
                run_id,
                "approval_granted",
                "user",
                {
                    "approval_id": approval_id,
                    "patch_hash": patch_hash,
                    "evaluation_verdict_hash": evaluation_verdict_hash,
                    "action": action,
                    "actor_id": actor_id,
                    "expires_at": expires_at.isoformat(),
                    "repository": {
                        "owner": run["repository_owner"],
                        "name": run["repository_name"],
                        "installation_id": run["installation_id"],
                        "base_ref": run["base_ref"],
                        "base_sha": run["base_sha"],
                    },
                },
            )
            connection.commit()
        return self.get_approval(approval_id)

    def get_approval(self, approval_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM approvals WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()
        if row is None:
            raise ApprovalError("approval not found")
        return dict(row)

    def request_publication(
        self,
        run_id: str,
        approval_id: str,
        patch_hash: str,
        title: str,
        body: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        patch_hash = validate_sha256(patch_hash, "patch_hash")
        title, body = validate_pull_request_text(title, body)
        idempotency_key = validate_idempotency_key(idempotency_key)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM publication_jobs WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["run_id"]) != run_id
                    or existing["approval_id"] != approval_id
                    or existing["patch_hash"] != patch_hash
                    or existing["pull_request_title"] != title
                    or existing["pull_request_body"] != body
                ):
                    raise PublicationConflictError(
                        "publication key was already used for a different request"
                    )
                connection.commit()
                return dict(existing)

            run = self._require_run(connection, run_id)
            if RunState(run["state"]) != RunState.AWAITING_APPROVAL:
                raise PublicationConflictError("run is not awaiting approval")
            approval = connection.execute(
                "SELECT * FROM approvals WHERE approval_id = ? AND run_id = ?",
                (approval_id, run_id),
            ).fetchone()
            if approval is None:
                raise ApprovalError("approval not found for run")
            if datetime.fromisoformat(str(approval["expires_at"])) <= now_utc():
                raise ApprovalExpiredError("approval has expired")
            expected = (
                run["evaluated_patch_hash"],
                run["evaluation_verdict_hash"],
                run["repository_owner"],
                run["repository_name"],
                run["installation_id"],
                run["base_ref"],
                run["base_sha"],
            )
            actual = (
                approval["patch_hash"],
                approval["evaluation_verdict_hash"],
                approval["repository_owner"],
                approval["repository_name"],
                approval["installation_id"],
                approval["base_ref"],
                approval["base_sha"],
            )
            if expected != actual or patch_hash != approval["patch_hash"]:
                raise ApprovalEvidenceMismatchError(
                    "approval is stale or does not match the publication"
                )
            if approval["action"] != ApprovalAction.CREATE_PULL_REQUEST:
                raise ApprovalError("approval does not authorize a pull request")
            prior = connection.execute(
                "SELECT * FROM publication_jobs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if prior is not None:
                raise PublicationConflictError(
                    "run already has a publication request"
                )

            publication_id = str(uuid.uuid4())
            branch_name = f"forge/{run_id[:8]}-{patch_hash[:12]}"
            created_at = now_iso()
            connection.execute(
                """
                INSERT INTO publication_jobs(
                    publication_id, idempotency_key, run_id, approval_id,
                    patch_hash, repository_owner, repository_name,
                    installation_id, base_sha, branch_name,
                    base_ref,
                    pull_request_title, pull_request_body, status, attempt,
                    available_at, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    'PENDING', 0, ?, ?, ?
                )
                """,
                (
                    publication_id,
                    idempotency_key,
                    run_id,
                    approval_id,
                    patch_hash,
                    run["repository_owner"],
                    run["repository_name"],
                    run["installation_id"],
                    run["base_sha"],
                    branch_name,
                    run["base_ref"],
                    title,
                    body,
                    created_at,
                    created_at,
                    created_at,
                ),
            )
            connection.execute(
                """
                UPDATE runs
                SET state = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (RunState.PUBLISHING, created_at, run_id),
            )
            self._append_event(
                connection,
                run_id,
                "publication_requested",
                "api",
                {
                    "publication_id": publication_id,
                    "approval_id": approval_id,
                    "patch_hash": patch_hash,
                    "base_sha": run["base_sha"],
                    "base_ref": run["base_ref"],
                    "branch_name": branch_name,
                    "idempotency_key": idempotency_key,
                },
            )
            self._append_event(
                connection,
                run_id,
                "state_changed",
                "api",
                {
                    "from_state": RunState.AWAITING_APPROVAL,
                    "to_state": RunState.PUBLISHING,
                    "publication_id": publication_id,
                },
            )
            connection.commit()
        return self.get_publication(publication_id)

    def get_publication(self, publication_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM publication_jobs WHERE publication_id = ?",
                (publication_id,),
            ).fetchone()
        if row is None:
            raise PublicationConflictError("publication not found")
        return dict(row)

    def claim_publication(
        self, publisher_id: str, lease_seconds: float = 30
    ) -> dict[str, Any] | None:
        publisher_id = validate_actor_id(publisher_id)
        expires_at = expiry_iso(lease_seconds)
        claimed_at = now_iso()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM publication_jobs
                WHERE (status = 'PENDING' AND available_at <= ?)
                   OR (
                       status = 'CLAIMED'
                       AND lease_expires_at IS NOT NULL
                       AND lease_expires_at <= ?
                   )
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (claimed_at, claimed_at),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            publication_id = str(row["publication_id"])
            attempt = int(row["attempt"]) + 1
            connection.execute(
                """
                UPDATE publication_jobs
                SET status = 'CLAIMED', attempt = ?, lease_owner = ?,
                    lease_expires_at = ?, updated_at = ?
                WHERE publication_id = ?
                """,
                (
                    attempt,
                    publisher_id,
                    expires_at,
                    claimed_at,
                    publication_id,
                ),
            )
            self._append_event(
                connection,
                str(row["run_id"]),
                "publication_claimed",
                "publisher",
                {
                    "publication_id": publication_id,
                    "publisher_id": publisher_id,
                    "attempt": attempt,
                },
            )
            connection.commit()
        return self.get_publication(publication_id)

    def renew_publication_lease(
        self,
        publication_id: str,
        publisher_id: str,
        lease_seconds: float = 30,
    ) -> dict[str, Any]:
        publisher_id = validate_actor_id(publisher_id)
        expires_at = expiry_iso(lease_seconds)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            publication = connection.execute(
                "SELECT * FROM publication_jobs WHERE publication_id = ?",
                (publication_id,),
            ).fetchone()
            if (
                publication is None
                or publication["status"] != "CLAIMED"
                or publication["lease_owner"] != publisher_id
            ):
                raise PublicationConflictError(
                    "publisher does not own the publication lease"
                )
            connection.execute(
                """
                UPDATE publication_jobs
                SET lease_expires_at = ?, updated_at = ?
                WHERE publication_id = ?
                """,
                (expires_at, now_iso(), publication_id),
            )
            connection.commit()
        return self.get_publication(publication_id)

    def record_publication_head(
        self, publication_id: str, publisher_id: str, head_sha: str
    ) -> dict[str, Any]:
        publisher_id = validate_actor_id(publisher_id)
        head_sha = validate_git_commit_sha(head_sha, "head_sha")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            publication = connection.execute(
                "SELECT * FROM publication_jobs WHERE publication_id = ?",
                (publication_id,),
            ).fetchone()
            if (
                publication is None
                or publication["status"] != "CLAIMED"
                or publication["lease_owner"] != publisher_id
            ):
                raise PublicationConflictError(
                    "publisher does not own the publication lease"
                )
            recorded = publication["github_head_sha"]
            if recorded is not None and recorded != head_sha:
                raise PublicationConflictError(
                    "publication already recorded a different head commit"
                )
            if recorded is None:
                connection.execute(
                    """
                    UPDATE publication_jobs
                    SET github_head_sha = ?, updated_at = ?
                    WHERE publication_id = ?
                    """,
                    (head_sha, now_iso(), publication_id),
                )
                self._append_event(
                    connection,
                    str(publication["run_id"]),
                    "publication_branch_ready",
                    "publisher",
                    {
                        "publication_id": publication_id,
                        "branch_name": publication["branch_name"],
                        "head_sha": head_sha,
                    },
                )
            connection.commit()
        return self.get_publication(publication_id)

    def complete_publication(
        self,
        publication_id: str,
        publisher_id: str,
        pull_request_number: int,
        pull_request_url: str,
        head_sha: str,
    ) -> dict[str, Any]:
        publisher_id = validate_actor_id(publisher_id)
        pull_request_number, pull_request_url, head_sha = (
            validate_publication_result(
                pull_request_number, pull_request_url, head_sha
            )
        )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            publication = connection.execute(
                "SELECT * FROM publication_jobs WHERE publication_id = ?",
                (publication_id,),
            ).fetchone()
            if publication is None:
                raise PublicationConflictError("publication not found")
            if publication["status"] == "COMPLETED":
                expected = (
                    publication["github_pull_request_number"],
                    publication["github_pull_request_url"],
                    publication["github_head_sha"],
                )
                if expected != (
                    pull_request_number,
                    pull_request_url,
                    head_sha,
                ):
                    raise PublicationConflictError(
                        "completed publication has different GitHub evidence"
                    )
                connection.commit()
                return dict(publication)
            if (
                publication["status"] != "CLAIMED"
                or publication["lease_owner"] != publisher_id
            ):
                raise PublicationConflictError(
                    "publisher does not own the publication lease"
                )
            if (
                publication["github_head_sha"] is not None
                and publication["github_head_sha"] != head_sha
            ):
                raise PublicationConflictError(
                    "pull request head differs from the recorded branch"
                )
            run = self._require_run(connection, str(publication["run_id"]))
            if RunState(run["state"]) != RunState.PUBLISHING:
                raise PublicationConflictError("run is not publishing")
            completed_at = now_iso()
            connection.execute(
                """
                UPDATE publication_jobs
                SET status = 'COMPLETED', lease_owner = NULL,
                    lease_expires_at = NULL, github_pull_request_number = ?,
                    github_pull_request_url = ?, github_head_sha = ?,
                    updated_at = ?
                WHERE publication_id = ?
                """,
                (
                    pull_request_number,
                    pull_request_url,
                    head_sha,
                    completed_at,
                    publication_id,
                ),
            )
            connection.execute(
                "UPDATE runs SET state = ?, updated_at = ? WHERE run_id = ?",
                (RunState.COMPLETED, completed_at, str(publication["run_id"])),
            )
            self._append_event(
                connection,
                str(publication["run_id"]),
                "pull_request_created",
                "publisher",
                {
                    "publication_id": publication_id,
                    "pull_request_number": pull_request_number,
                    "pull_request_url": pull_request_url,
                    "head_sha": head_sha,
                    "patch_hash": publication["patch_hash"],
                },
            )
            self._append_event(
                connection,
                str(publication["run_id"]),
                "state_changed",
                "publisher",
                {
                    "from_state": RunState.PUBLISHING,
                    "to_state": RunState.COMPLETED,
                    "publication_id": publication_id,
                },
            )
            connection.commit()
        return self.get_publication(publication_id)

    def fail_publication(
        self,
        publication_id: str,
        publisher_id: str,
        failure_code: str,
        *,
        permanent: bool,
    ) -> dict[str, Any]:
        publisher_id = validate_actor_id(publisher_id)
        if not failure_code or len(failure_code) > 64 or not failure_code.replace(
            "_", ""
        ).isalnum():
            raise ValueError("failure_code is invalid")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            publication = connection.execute(
                "SELECT * FROM publication_jobs WHERE publication_id = ?",
                (publication_id,),
            ).fetchone()
            if (
                publication is None
                or publication["status"] != "CLAIMED"
                or publication["lease_owner"] != publisher_id
            ):
                raise PublicationConflictError(
                    "publisher does not own the publication lease"
                )
            status = "FAILED" if permanent else "PENDING"
            updated_at = now_iso()
            available_at = (
                updated_at
                if permanent
                else (
                    now_utc()
                    + timedelta(
                        seconds=publication_retry_delay_seconds(
                            int(publication["attempt"])
                        )
                    )
                ).isoformat()
            )
            connection.execute(
                """
                UPDATE publication_jobs
                SET status = ?, failure_code = ?, lease_owner = NULL,
                    lease_expires_at = NULL, available_at = ?, updated_at = ?
                WHERE publication_id = ?
                """,
                (
                    status,
                    failure_code,
                    available_at,
                    updated_at,
                    publication_id,
                ),
            )
            event_type = (
                "publication_failed" if permanent else "publication_retry_scheduled"
            )
            self._append_event(
                connection,
                str(publication["run_id"]),
                event_type,
                "publisher",
                {
                    "publication_id": publication_id,
                    "failure_code": failure_code,
                    "attempt": publication["attempt"],
                    "available_at": available_at,
                },
            )
            if permanent:
                connection.execute(
                    "UPDATE runs SET state = ?, updated_at = ? WHERE run_id = ?",
                    (RunState.FAILED, updated_at, str(publication["run_id"])),
                )
                self._append_event(
                    connection,
                    str(publication["run_id"]),
                    "state_changed",
                    "publisher",
                    {
                        "from_state": RunState.PUBLISHING,
                        "to_state": RunState.FAILED,
                        "publication_id": publication_id,
                        "reason": failure_code,
                    },
                )
            connection.commit()
        return self.get_publication(publication_id)

    def _finalize_abandoned_cancellations(
        self, connection: sqlite3.Connection, observed_at: str
    ) -> None:
        rows = connection.execute(
            """
            SELECT * FROM runs
            WHERE state IN (?, ?, ?)
              AND cancellation_requested_at IS NOT NULL
              AND (lease_expires_at IS NULL OR lease_expires_at <= ?)
            """,
            (
                RunState.SNAPSHOTTING,
                RunState.EXECUTING,
                RunState.EVALUATING,
                observed_at,
            ),
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
