from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class SnapshotMismatchError(RuntimeError):
    """Raised when indexed content changes before it is retrieved."""


@dataclass(frozen=True)
class Symbol:
    name: str
    kind: str
    line: int

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "kind": self.kind, "line": self.line}


@dataclass(frozen=True)
class ImportReference:
    target: str
    line: int
    resolved_path: str | None = None
    level: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "line": self.line,
            "resolved_path": self.resolved_path,
        }


@dataclass(frozen=True)
class FileEntry:
    path: str
    size_bytes: int
    sha256: str
    language: str
    line_count: int | None
    indexed: bool
    exclusion_reason: str | None
    symbols: tuple[Symbol, ...] = ()
    imports: tuple[ImportReference, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "language": self.language,
            "line_count": self.line_count,
            "indexed": self.indexed,
            "exclusion_reason": self.exclusion_reason,
            "symbols": [symbol.to_dict() for symbol in self.symbols],
            "imports": [reference.to_dict() for reference in self.imports],
        }


@dataclass(frozen=True)
class RepositoryManifest:
    schema_version: int
    snapshot_hash: str
    manifest_hash: str
    files: tuple[FileEntry, ...]
    ignored_paths: tuple[str, ...]
    languages: dict[str, int]
    build_systems: tuple[str, ...]
    test_commands: tuple[str, ...]
    dependencies: dict[str, tuple[str, ...]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "snapshot_hash": self.snapshot_hash,
            "manifest_hash": self.manifest_hash,
            "files": [entry.to_dict() for entry in self.files],
            "ignored_paths": list(self.ignored_paths),
            "languages": dict(self.languages),
            "build_systems": list(self.build_systems),
            "test_commands": list(self.test_commands),
            "dependencies": {
                path: list(neighbors)
                for path, neighbors in self.dependencies.items()
            },
        }

    def brief(self) -> dict[str, Any]:
        indexed = sum(entry.indexed for entry in self.files)
        binary = sum(
            entry.exclusion_reason == "binary" for entry in self.files
        )
        oversized = sum(
            entry.exclusion_reason == "oversized" for entry in self.files
        )
        return {
            "snapshot_hash": self.snapshot_hash,
            "manifest_hash": self.manifest_hash,
            "file_count": len(self.files),
            "indexed_file_count": indexed,
            "ignored_path_count": len(self.ignored_paths),
            "binary_file_count": binary,
            "oversized_file_count": oversized,
            "total_bytes": sum(entry.size_bytes for entry in self.files),
            "languages": dict(self.languages),
            "build_systems": list(self.build_systems),
            "test_commands": list(self.test_commands),
        }


@dataclass(frozen=True)
class SearchResult:
    snapshot_hash: str
    path: str
    start_line: int
    end_line: int
    kind: str
    snippet: str
    score: float = 1.0

    @property
    def provenance(self) -> str:
        return (
            f"{self.snapshot_hash}:{self.path}:"
            f"{self.start_line}-{self.end_line}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_hash": self.snapshot_hash,
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "kind": self.kind,
            "snippet": self.snippet,
            "score": self.score,
            "provenance": self.provenance,
        }


@dataclass(frozen=True)
class ContextItem:
    provenance: str
    content: str
    character_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "provenance": self.provenance,
            "content": self.content,
            "character_count": self.character_count,
        }


@dataclass(frozen=True)
class ContextPack:
    snapshot_hash: str
    budget_characters: int
    used_characters: int
    truncated: bool
    items: tuple[ContextItem, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_hash": self.snapshot_hash,
            "budget_characters": self.budget_characters,
            "used_characters": self.used_characters,
            "truncated": self.truncated,
            "items": [item.to_dict() for item in self.items],
        }
