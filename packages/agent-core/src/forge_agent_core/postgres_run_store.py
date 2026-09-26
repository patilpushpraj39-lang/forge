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
    publication_retry_delay_seconds,
)
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
        repository: RepositoryTarget | None = None,
        source_snapshot: SourceSnapshot | None = None,
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
                    repository_owner,
                    repository_name,
                    installation_id,
                    base_ref,
                    base_sha,
                    source_snapshot_sha256,
                    source_snapshot_size_bytes,
                    source_snapshot_media_type,
                    state
                ) values (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s
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
            else:
                connection.execute(
                    """
                    update runs
                    set state = %s,
                        evaluated_patch_hash = %s,
                        evaluation_verdict_hash = %s,
                        updated_at = clock_timestamp(),
                        row_version = row_version + 1,
                        lease_owner = null,
                        lease_expires_at = null
                    where run_id = %s
                    """,
                    (target, *evaluation_hashes, run_id),
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
            if current == RunState.PUBLISHING:
                raise PublicationConflictError(
                    "publication has already started and cannot be cancelled"
                )

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
        approval_id: str | None = None
        with self._connection() as connection, connection.transaction():
            existing = connection.execute(
                "select * from approvals where approval_key = %s",
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
                return self._approval_from_row(existing)

            run = self._require_run(connection, run_id, for_update=True)
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
            expires_at = datetime.now(UTC) + timedelta(
                seconds=expires_in_seconds
            )
            created_at = datetime.now(UTC)
            connection.execute(
                """
                insert into approvals(
                    approval_id, approval_key, run_id, patch_hash,
                    evaluation_verdict_hash, repository_owner, repository_name,
                    installation_id, base_ref, base_sha, action, actor_id, expires_at,
                    created_at
                ) values (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
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
                    expires_at,
                    created_at,
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
        assert approval_id is not None
        return self.get_approval(approval_id)

    def get_approval(self, approval_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                "select * from approvals where approval_id = %s",
                (approval_id,),
            ).fetchone()
        if row is None:
            raise ApprovalError("approval not found")
        return self._approval_from_row(row)

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
        publication_id: str | None = None
        with self._connection() as connection, connection.transaction():
            existing = connection.execute(
                "select * from publication_jobs where idempotency_key = %s",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["run_id"]) != run_id
                    or str(existing["approval_id"]) != approval_id
                    or existing["patch_hash"] != patch_hash
                    or existing["pull_request_title"] != title
                    or existing["pull_request_body"] != body
                ):
                    raise PublicationConflictError(
                        "publication key was already used for a different request"
                    )
                return self._publication_from_row(existing)

            run = self._require_run(connection, run_id, for_update=True)
            if RunState(run["state"]) != RunState.AWAITING_APPROVAL:
                raise PublicationConflictError("run is not awaiting approval")
            approval = connection.execute(
                """
                select * from approvals
                where approval_id = %s and run_id = %s
                """,
                (approval_id, run_id),
            ).fetchone()
            if approval is None:
                raise ApprovalError("approval not found for run")
            if approval["expires_at"] <= datetime.now(UTC):
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
                "select publication_id from publication_jobs where run_id = %s",
                (run_id,),
            ).fetchone()
            if prior is not None:
                raise PublicationConflictError(
                    "run already has a publication request"
                )

            publication_id = str(uuid.uuid4())
            branch_name = f"forge/{run_id[:8]}-{patch_hash[:12]}"
            connection.execute(
                """
                insert into publication_jobs(
                    publication_id, idempotency_key, run_id, approval_id,
                    patch_hash, repository_owner, repository_name,
                    installation_id, base_ref, base_sha, branch_name,
                    pull_request_title, pull_request_body, status, attempt,
                    available_at
                ) values (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, 'PENDING', 0, clock_timestamp()
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
                    run["base_ref"],
                    run["base_sha"],
                    branch_name,
                    title,
                    body,
                ),
            )
            connection.execute(
                """
                update runs
                set state = %s,
                    updated_at = clock_timestamp(),
                    row_version = row_version + 1
                where run_id = %s
                """,
                (RunState.PUBLISHING, run_id),
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
                topic="publication.requested",
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
        assert publication_id is not None
        return self.get_publication(publication_id)

    def get_publication(self, publication_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                "select * from publication_jobs where publication_id = %s",
                (publication_id,),
            ).fetchone()
        if row is None:
            raise PublicationConflictError("publication not found")
        return self._publication_from_row(row)

    def claim_publication(
        self, publisher_id: str, lease_seconds: float = 30
    ) -> dict[str, Any] | None:
        publisher_id = validate_actor_id(publisher_id)
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        lease_duration = timedelta(seconds=lease_seconds)
        publication_id: str | None = None
        with self._connection() as connection, connection.transaction():
            row = connection.execute(
                """
                select * from publication_jobs
                where (status = 'PENDING' and available_at <= clock_timestamp())
                   or (
                       status = 'CLAIMED'
                       and lease_expires_at is not null
                       and lease_expires_at <= clock_timestamp()
                   )
                order by created_at asc, publication_id asc
                limit 1
                for update skip locked
                """
            ).fetchone()
            if row is None:
                return None
            publication_id = str(row["publication_id"])
            attempt = int(row["attempt"]) + 1
            connection.execute(
                """
                update publication_jobs
                set status = 'CLAIMED', attempt = %s, lease_owner = %s,
                    lease_expires_at = clock_timestamp() + %s,
                    updated_at = clock_timestamp()
                where publication_id = %s
                """,
                (attempt, publisher_id, lease_duration, publication_id),
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
        assert publication_id is not None
        return self.get_publication(publication_id)

    def renew_publication_lease(
        self,
        publication_id: str,
        publisher_id: str,
        lease_seconds: float = 30,
    ) -> dict[str, Any]:
        publisher_id = validate_actor_id(publisher_id)
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        with self._connection() as connection, connection.transaction():
            publication = connection.execute(
                """
                select * from publication_jobs
                where publication_id = %s
                for update
                """,
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
                update publication_jobs
                set lease_expires_at = clock_timestamp() + %s,
                    updated_at = clock_timestamp()
                where publication_id = %s
                """,
                (timedelta(seconds=lease_seconds), publication_id),
            )
        return self.get_publication(publication_id)

    def record_publication_head(
        self, publication_id: str, publisher_id: str, head_sha: str
    ) -> dict[str, Any]:
        publisher_id = validate_actor_id(publisher_id)
        head_sha = validate_git_commit_sha(head_sha, "head_sha")
        with self._connection() as connection, connection.transaction():
            publication = connection.execute(
                """
                select * from publication_jobs
                where publication_id = %s
                for update
                """,
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
                    update publication_jobs
                    set github_head_sha = %s, updated_at = clock_timestamp()
                    where publication_id = %s
                    """,
                    (head_sha, publication_id),
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
        with self._connection() as connection, connection.transaction():
            publication = connection.execute(
                """
                select * from publication_jobs
                where publication_id = %s
                for update
                """,
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
                return self._publication_from_row(publication)
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
            run_id = str(publication["run_id"])
            run = self._require_run(connection, run_id, for_update=True)
            if RunState(run["state"]) != RunState.PUBLISHING:
                raise PublicationConflictError("run is not publishing")
            connection.execute(
                """
                update publication_jobs
                set status = 'COMPLETED', lease_owner = null,
                    lease_expires_at = null, github_pull_request_number = %s,
                    github_pull_request_url = %s, github_head_sha = %s,
                    updated_at = clock_timestamp()
                where publication_id = %s
                """,
                (
                    pull_request_number,
                    pull_request_url,
                    head_sha,
                    publication_id,
                ),
            )
            connection.execute(
                """
                update runs
                set state = %s, updated_at = clock_timestamp(),
                    row_version = row_version + 1
                where run_id = %s
                """,
                (RunState.COMPLETED, run_id),
            )
            self._append_event(
                connection,
                run_id,
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
                run_id,
                "state_changed",
                "publisher",
                {
                    "from_state": RunState.PUBLISHING,
                    "to_state": RunState.COMPLETED,
                    "publication_id": publication_id,
                },
            )
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
        with self._connection() as connection, connection.transaction():
            publication = connection.execute(
                """
                select * from publication_jobs
                where publication_id = %s
                for update
                """,
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
            retry_delay = timedelta(
                seconds=publication_retry_delay_seconds(
                    int(publication["attempt"])
                )
            )
            connection.execute(
                """
                update publication_jobs
                set status = %s, failure_code = %s, lease_owner = null,
                    lease_expires_at = null,
                    available_at = case
                        when %s then clock_timestamp()
                        else clock_timestamp() + %s
                    end,
                    updated_at = clock_timestamp()
                where publication_id = %s
                """,
                (status, failure_code, permanent, retry_delay, publication_id),
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
                    "retry_delay_seconds": (
                        0 if permanent else int(retry_delay.total_seconds())
                    ),
                },
            )
            if permanent:
                connection.execute(
                    """
                    update runs
                    set state = %s, updated_at = clock_timestamp(),
                        row_version = row_version + 1
                    where run_id = %s
                    """,
                    (RunState.FAILED, str(publication["run_id"])),
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
        return self.get_publication(publication_id)

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
        *,
        topic: str = "run.event",
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
            (run_id, event["event_id"], topic, Jsonb(event)),
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

    @staticmethod
    def _approval_from_row(row: dict[str, Any]) -> dict[str, Any]:
        approval = dict(row)
        approval["approval_id"] = str(approval["approval_id"])
        approval["run_id"] = str(approval["run_id"])
        approval["expires_at"] = approval["expires_at"].isoformat()
        approval["created_at"] = approval["created_at"].isoformat()
        return approval

    @staticmethod
    def _publication_from_row(row: dict[str, Any]) -> dict[str, Any]:
        publication = dict(row)
        for key in ("publication_id", "run_id", "approval_id"):
            publication[key] = str(publication[key])
        for key in ("lease_expires_at", "created_at", "updated_at"):
            if publication[key] is not None:
                publication[key] = publication[key].isoformat()
        return publication
