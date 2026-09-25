from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol


class CommandStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True)
class SandboxHandle:
    sandbox_id: str
    snapshot_hash: str
    file_count: int


@dataclass(frozen=True)
class CommandResult:
    status: CommandStatus
    exit_code: int | None
    output: str


class SandboxNotFoundError(KeyError):
    pass


class SandboxController(Protocol):
    def create(self, repository_path: Path) -> SandboxHandle: ...

    def execute(
        self,
        sandbox_id: str,
        command: Sequence[str],
        timeout_seconds: float,
        should_cancel: Callable[[], bool],
        heartbeat: Callable[[], None],
        heartbeat_interval_seconds: float,
    ) -> CommandResult: ...

    def destroy(self, sandbox_id: str) -> None: ...
