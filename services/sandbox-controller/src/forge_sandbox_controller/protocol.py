from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from forge_repo_intelligence import (
    ContextPack,
    RepositoryManifest,
    SearchResult,
)


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

    def index_repository(self, sandbox_id: str) -> RepositoryManifest: ...

    def search_repository(
        self,
        sandbox_id: str,
        query: str,
        modes: Sequence[str] = ("path", "text", "symbol"),
        limit: int = 20,
    ) -> tuple[SearchResult, ...]: ...

    def dependency_neighborhood(
        self,
        sandbox_id: str,
        path: str,
        max_depth: int = 1,
        limit: int = 20,
    ) -> tuple[SearchResult, ...]: ...

    def rank_relevant_files(
        self,
        sandbox_id: str,
        objective: str,
        limit: int = 10,
    ) -> tuple[SearchResult, ...]: ...

    def build_context_pack(
        self,
        sandbox_id: str,
        results: Sequence[SearchResult],
        budget_characters: int,
    ) -> ContextPack: ...

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
