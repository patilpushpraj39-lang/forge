from __future__ import annotations

from typing import Any, Protocol

from .run_store import RunState


class RunStoreProtocol(Protocol):
    def create_run(self, repository_path: str) -> dict[str, Any]: ...

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

    def renew_lease(
        self, run_id: str, worker_id: str, lease_seconds: float = 30
    ) -> dict[str, Any]: ...

    def request_cancellation(self, run_id: str) -> dict[str, Any]: ...

    def cancel_run(self, run_id: str) -> dict[str, Any]: ...

    def is_cancellation_requested(self, run_id: str) -> bool: ...

    def acknowledge_cancellation(
        self, run_id: str, worker_id: str
    ) -> dict[str, Any]: ...
