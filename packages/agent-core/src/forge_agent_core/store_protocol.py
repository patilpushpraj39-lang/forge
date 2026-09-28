from __future__ import annotations

from typing import Any, Protocol

from .model_runtime import BudgetLimits
from .publication_contract import ApprovalAction, RepositoryTarget
from .run_contract import DEFAULT_RUN_OBJECTIVE
from .run_store import RunState
from .source_contract import SourceSnapshot


class RunStoreProtocol(Protocol):
    def create_run(
        self,
        repository_path: str,
        objective: str = DEFAULT_RUN_OBJECTIVE,
        budgets: BudgetLimits | None = None,
        repository: RepositoryTarget | None = None,
        source_snapshot: SourceSnapshot | None = None,
    ) -> dict[str, Any]: ...

    def get_run(self, run_id: str) -> dict[str, Any]: ...

    def list_events(
        self, run_id: str, after_sequence: int = 0
    ) -> list[dict[str, Any]]: ...

    def append_event(
        self,
        run_id: str,
        event_type: str,
        actor: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]: ...

    def transition(
        self,
        run_id: str,
        expected: RunState,
        target: RunState,
        actor: str,
        payload: dict[str, Any] | None = None,
        lease_owner: str | None = None,
    ) -> dict[str, Any]: ...

    def claim_next_run(
        self, worker_id: str, lease_seconds: float = 30
    ) -> dict[str, Any] | None: ...

    def claim_run(
        self, run_id: str, worker_id: str, lease_seconds: float = 30
    ) -> dict[str, Any] | None: ...

    def renew_lease(
        self, run_id: str, worker_id: str, lease_seconds: float = 30
    ) -> dict[str, Any]: ...

    def request_cancellation(self, run_id: str) -> dict[str, Any]: ...

    def cancel_run(self, run_id: str) -> dict[str, Any]: ...

    def is_cancellation_requested(self, run_id: str) -> bool: ...

    def acknowledge_cancellation(
        self, run_id: str, worker_id: str
    ) -> dict[str, Any]: ...

    def grant_approval(
        self,
        run_id: str,
        patch_hash: str,
        evaluation_verdict_hash: str,
        actor_id: str,
        approval_key: str,
        expires_in_seconds: int = 900,
        action: ApprovalAction = ApprovalAction.CREATE_PULL_REQUEST,
    ) -> dict[str, Any]: ...

    def get_approval(self, approval_id: str) -> dict[str, Any]: ...

    def request_publication(
        self,
        run_id: str,
        approval_id: str,
        patch_hash: str,
        title: str,
        body: str,
        idempotency_key: str,
    ) -> dict[str, Any]: ...

    def get_publication(self, publication_id: str) -> dict[str, Any]: ...

    def claim_publication(
        self, publisher_id: str, lease_seconds: float = 30
    ) -> dict[str, Any] | None: ...

    def renew_publication_lease(
        self,
        publication_id: str,
        publisher_id: str,
        lease_seconds: float = 30,
    ) -> dict[str, Any]: ...

    def record_publication_head(
        self, publication_id: str, publisher_id: str, head_sha: str
    ) -> dict[str, Any]: ...

    def complete_publication(
        self,
        publication_id: str,
        publisher_id: str,
        pull_request_number: int,
        pull_request_url: str,
        head_sha: str,
    ) -> dict[str, Any]: ...

    def fail_publication(
        self,
        publication_id: str,
        publisher_id: str,
        failure_code: str,
        *,
        permanent: bool,
    ) -> dict[str, Any]: ...
