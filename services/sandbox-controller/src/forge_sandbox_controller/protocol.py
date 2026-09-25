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
    OUTPUT_LIMIT = "output_limit"
    RESOURCE_LIMIT = "resource_limit"


@dataclass(frozen=True)
class ArtifactRef:
    sha256: str
    size_bytes: int
    media_type: str

    def to_dict(self) -> dict[str, str | int]:
        return {
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "media_type": self.media_type,
        }


@dataclass(frozen=True)
class SandboxHandle:
    sandbox_id: str
    snapshot_hash: str
    file_count: int
    snapshot_artifact: ArtifactRef | None = None


@dataclass(frozen=True)
class PatchArtifact:
    artifact: ArtifactRef
    changed_paths: tuple[str, ...]

    @property
    def diff_hash(self) -> str:
        return self.artifact.sha256


@dataclass(frozen=True)
class AppliedPatch:
    patch_hash: str
    changed_paths: tuple[str, ...]


@dataclass(frozen=True)
class CommandResult:
    status: CommandStatus
    exit_code: int | None
    output: str
    output_truncated: bool = False


class SandboxNotFoundError(KeyError):
    pass


class ArtifactNotFoundError(KeyError):
    pass


class InvalidPatchError(ValueError):
    pass


class ArtifactStore(Protocol):
    def put_bytes(self, content: bytes, media_type: str) -> ArtifactRef: ...

    def put_file(self, source: Path, media_type: str) -> ArtifactRef: ...

    def read_bytes(self, reference: ArtifactRef, max_bytes: int) -> bytes: ...


class SandboxController(Protocol):
    def create(self, repository_path: Path) -> SandboxHandle: ...

    def create_from_snapshot(self, snapshot: ArtifactRef) -> SandboxHandle: ...

    def index_repository(self, sandbox_id: str) -> RepositoryManifest: ...

    def search_repository(
        self,
        sandbox_id: str,
        query: str,
        modes: Sequence[str] = ("path", "text", "symbol"),
        limit: int = 20,
    ) -> tuple[SearchResult, ...]: ...

    def read_file(
        self,
        sandbox_id: str,
        path: str,
        start_line: int = 1,
        end_line: int | None = None,
        max_characters: int = 20_000,
    ) -> SearchResult: ...

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

    def apply_patch(
        self,
        sandbox_id: str,
        patch: bytes,
        expected_sha256: str | None = None,
    ) -> AppliedPatch: ...

    def diff(self, sandbox_id: str) -> PatchArtifact: ...

    def read_artifact(
        self, reference: ArtifactRef, max_bytes: int
    ) -> bytes: ...

    def execute(
        self,
        sandbox_id: str,
        command: Sequence[str],
        timeout_seconds: float,
        should_cancel: Callable[[], bool],
        heartbeat: Callable[[], None],
        heartbeat_interval_seconds: float,
        output_limit_bytes: int = 4096,
    ) -> CommandResult: ...

    def destroy(self, sandbox_id: str) -> None: ...
