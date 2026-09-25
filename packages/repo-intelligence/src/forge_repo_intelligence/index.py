from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
import os
import re
from collections import Counter, deque
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, Sequence

from .models import (
    ContextItem,
    ContextPack,
    FileEntry,
    ImportReference,
    RepositoryManifest,
    SearchResult,
    SnapshotMismatchError,
    Symbol,
)


BUILTIN_IGNORED_NAMES = {
    ".git",
    ".next",
    ".state",
    ".venv",
    ".artifacts",
    ".sandboxes",
    "__pycache__",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "vendor",
}

LANGUAGE_BY_SUFFIX = {
    ".c": "C",
    ".cc": "C++",
    ".cpp": "C++",
    ".css": "CSS",
    ".go": "Go",
    ".html": "HTML",
    ".java": "Java",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".json": "JSON",
    ".md": "Markdown",
    ".py": "Python",
    ".rs": "Rust",
    ".sh": "Shell",
    ".sql": "SQL",
    ".toml": "TOML",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".yaml": "YAML",
    ".yml": "YAML",
}

TYPESCRIPT_SYMBOL_PATTERN = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?"
    r"(?:(async)\s+)?(class|function|interface|type|enum|const|let|var)\s+"
    r"([A-Za-z_$][\w$]*)"
)
TYPESCRIPT_IMPORT_PATTERN = re.compile(
    r"(?:import|export)\s+(?:[\s\S]*?\s+from\s+)?[\"']([^\"']+)[\"']"
    r"|require\(\s*[\"']([^\"']+)[\"']\s*\)"
)
TOKEN_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
CAMEL_BOUNDARY_PATTERN = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
OBJECTIVE_STOP_WORDS = {
    "and",
    "are",
    "behavior",
    "before",
    "change",
    "continue",
    "current",
    "field",
    "from",
    "must",
    "should",
    "the",
    "their",
    "this",
    "when",
    "while",
    "with",
}


@dataclass(frozen=True)
class IgnoreRule:
    pattern: str
    negated: bool
    directory_only: bool
    anchored: bool


class IgnoreMatcher:
    """Small, deterministic root .gitignore matcher for common repository rules."""

    def __init__(self, rules: Sequence[IgnoreRule]) -> None:
        self.rules = tuple(rules)
        self.has_negations = any(rule.negated for rule in self.rules)

    @classmethod
    def from_repository(cls, root: Path) -> IgnoreMatcher:
        gitignore = root / ".gitignore"
        if not gitignore.is_file():
            return cls(())
        rules: list[IgnoreRule] = []
        for raw_line in gitignore.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            negated = line.startswith("!")
            if negated:
                line = line[1:]
            directory_only = line.endswith("/")
            line = line.rstrip("/")
            anchored = line.startswith("/")
            line = line.lstrip("/")
            if line:
                rules.append(
                    IgnoreRule(line, negated, directory_only, anchored)
                )
        return cls(rules)

    def matches(self, path: str, *, is_directory: bool) -> bool:
        normalized = path.strip("/")
        ignored = False
        for rule in self.rules:
            if rule.directory_only and not is_directory:
                if not (
                    normalized == rule.pattern
                    or normalized.startswith(f"{rule.pattern}/")
                ):
                    continue
            if self._rule_matches(rule, normalized):
                ignored = not rule.negated
        return ignored

    @staticmethod
    def _rule_matches(rule: IgnoreRule, path: str) -> bool:
        pattern = rule.pattern
        if rule.anchored or "/" in pattern:
            return fnmatch.fnmatchcase(path, pattern) or path.startswith(
                f"{pattern}/"
            )
        return any(
            fnmatch.fnmatchcase(part, pattern) for part in path.split("/")
        )


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _detect_language(path: str) -> str:
    name = Path(path).name.lower()
    if name == "dockerfile" or name.startswith("dockerfile."):
        return "Dockerfile"
    return LANGUAGE_BY_SUFFIX.get(Path(path).suffix.lower(), "Other")


def _looks_binary(prefix: bytes) -> bool:
    if b"\0" in prefix:
        return True
    try:
        prefix.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def _tokens(value: str) -> set[str]:
    expanded = CAMEL_BOUNDARY_PATTERN.sub(" ", value.replace("_", " "))
    normalized: set[str] = set()
    for match in TOKEN_PATTERN.finditer(expanded):
        token = match.group(0).casefold()
        if len(token) < 3 or token in OBJECTIVE_STOP_WORDS:
            continue
        if token.endswith("ies") and len(token) > 4:
            token = f"{token[:-3]}y"
        elif token.endswith("s") and not token.endswith("ss") and len(token) > 3:
            token = token[:-1]
        normalized.add(token)
    return normalized


def _python_metadata(content: str) -> tuple[tuple[Symbol, ...], tuple[ImportReference, ...]]:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return (), ()
    symbols: list[Symbol] = []
    imports: list[ImportReference] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(Symbol(node.name, "function", node.lineno))
        elif isinstance(node, ast.ClassDef):
            symbols.append(Symbol(node.name, "class", node.lineno))
        elif isinstance(node, ast.Import):
            imports.extend(
                ImportReference(alias.name, node.lineno) for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            target = node.module or ""
            if not target:
                imports.extend(
                    ImportReference(alias.name, node.lineno, level=node.level)
                    for alias in node.names
                )
            else:
                imports.append(
                    ImportReference(target, node.lineno, level=node.level)
                )
    return (
        tuple(sorted(symbols, key=lambda item: (item.line, item.name))),
        tuple(sorted(imports, key=lambda item: (item.line, item.target))),
    )


def _typescript_metadata(
    content: str,
) -> tuple[tuple[Symbol, ...], tuple[ImportReference, ...]]:
    symbols: list[Symbol] = []
    imports: list[ImportReference] = []
    for line_number, line in enumerate(content.splitlines(), start=1):
        symbol_match = TYPESCRIPT_SYMBOL_PATTERN.search(line)
        if symbol_match:
            kind = symbol_match.group(2)
            symbols.append(Symbol(symbol_match.group(3), kind, line_number))
        for import_match in TYPESCRIPT_IMPORT_PATTERN.finditer(line):
            target = import_match.group(1) or import_match.group(2)
            imports.append(ImportReference(target, line_number))
    return tuple(symbols), tuple(imports)


def _extract_metadata(
    language: str, content: str
) -> tuple[tuple[Symbol, ...], tuple[ImportReference, ...]]:
    if language == "Python":
        return _python_metadata(content)
    if language in {"TypeScript", "JavaScript"}:
        return _typescript_metadata(content)
    return (), ()


def _resolve_import(
    source_path: str,
    reference: ImportReference,
    known_paths: set[str],
    language: str,
) -> str | None:
    source = Path(source_path)
    candidates: list[str] = []
    if language == "Python":
        module_parts = reference.target.split(".") if reference.target else []
        if reference.level:
            base = list(source.parent.parts)
            trim = max(0, reference.level - 1)
            if trim:
                base = base[:-trim]
            parts = [*base, *module_parts]
        else:
            parts = module_parts
        if parts:
            module_path = "/".join(parts)
            candidates.extend(
                [f"{module_path}.py", f"{module_path}/__init__.py"]
            )
            candidates.extend(
                path
                for path in known_paths
                if path.endswith(f"/{module_path}.py")
                or path.endswith(f"/{module_path}/__init__.py")
            )
    elif reference.target.startswith("."):
        base = (source.parent / reference.target).as_posix()
        candidates.extend(
            [
                base,
                f"{base}.ts",
                f"{base}.tsx",
                f"{base}.js",
                f"{base}.jsx",
                f"{base}/index.ts",
                f"{base}/index.tsx",
                f"{base}/index.js",
            ]
        )
    return next((candidate for candidate in candidates if candidate in known_paths), None)


def _detect_build_configuration(paths: set[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    systems: list[str] = []
    commands: list[str] = []
    names = {Path(path).name for path in paths}
    if "pyproject.toml" in names:
        systems.append("Python/pyproject")
        commands.append("python -m pytest")
    elif {"requirements.txt", "setup.py", "setup.cfg"} & names:
        systems.append("Python")
        commands.append("python -m pytest")
    if "pnpm-lock.yaml" in names:
        systems.append("Node.js/pnpm")
        commands.append("pnpm test")
    elif "yarn.lock" in names:
        systems.append("Node.js/Yarn")
        commands.append("yarn test")
    elif "package-lock.json" in names or "package.json" in names:
        systems.append("Node.js/npm")
        commands.append("npm test")
    if "go.mod" in names:
        systems.append("Go modules")
        commands.append("go test ./...")
    if "Cargo.toml" in names:
        systems.append("Rust/Cargo")
        commands.append("cargo test")
    return tuple(systems), tuple(commands)


class RepositoryIndexer:
    def __init__(
        self,
        *,
        max_file_bytes: int = 256_000,
        max_files: int = 20_000,
    ) -> None:
        if max_file_bytes <= 0 or max_files <= 0:
            raise ValueError("index limits must be positive")
        self.max_file_bytes = max_file_bytes
        self.max_files = max_files

    def build(self, root: Path, snapshot_hash: str) -> RepositoryIndex:
        root = root.resolve()
        if not root.is_dir():
            raise FileNotFoundError(root)
        matcher = IgnoreMatcher.from_repository(root)
        ignored_paths: list[str] = []
        entries: list[FileEntry] = []

        for directory, directory_names, file_names in os.walk(
            root, followlinks=False
        ):
            current = Path(directory)
            retained_directories: list[str] = []
            for name in sorted(directory_names):
                absolute = current / name
                relative = absolute.relative_to(root).as_posix()
                if name in BUILTIN_IGNORED_NAMES or absolute.is_symlink():
                    ignored_paths.append(f"{relative}/")
                    continue
                if matcher.matches(relative, is_directory=True):
                    ignored_paths.append(f"{relative}/")
                    if not matcher.has_negations:
                        continue
                retained_directories.append(name)
            directory_names[:] = retained_directories

            for name in sorted(file_names):
                absolute = current / name
                relative = absolute.relative_to(root).as_posix()
                if absolute.is_symlink() or matcher.matches(
                    relative, is_directory=False
                ):
                    ignored_paths.append(relative)
                    continue
                if len(entries) >= self.max_files:
                    raise ValueError(
                        f"repository exceeds the {self.max_files} file limit"
                    )
                size = absolute.stat().st_size
                digest = _sha256_file(absolute)
                language = _detect_language(relative)
                with absolute.open("rb") as source:
                    prefix = source.read(8192)
                if _looks_binary(prefix):
                    entries.append(
                        FileEntry(
                            relative,
                            size,
                            digest,
                            language,
                            None,
                            False,
                            "binary",
                        )
                    )
                    continue
                if size > self.max_file_bytes:
                    entries.append(
                        FileEntry(
                            relative,
                            size,
                            digest,
                            language,
                            None,
                            False,
                            "oversized",
                        )
                    )
                    continue
                content = absolute.read_text(encoding="utf-8")
                symbols, imports = _extract_metadata(language, content)
                entries.append(
                    FileEntry(
                        relative,
                        size,
                        digest,
                        language,
                        len(content.splitlines()),
                        True,
                        None,
                        symbols,
                        imports,
                    )
                )

        entries.sort(key=lambda entry: entry.path)
        known_paths = {entry.path for entry in entries}
        resolved_entries: list[FileEntry] = []
        dependencies: dict[str, tuple[str, ...]] = {}
        for entry in entries:
            resolved_imports = tuple(
                replace(
                    reference,
                    resolved_path=_resolve_import(
                        entry.path,
                        reference,
                        known_paths,
                        entry.language,
                    ),
                )
                for reference in entry.imports
            )
            resolved_entry = replace(entry, imports=resolved_imports)
            resolved_entries.append(resolved_entry)
            neighbors = sorted(
                {
                    reference.resolved_path
                    for reference in resolved_imports
                    if reference.resolved_path is not None
                }
            )
            dependencies[entry.path] = tuple(neighbors)

        languages = dict(
            sorted(
                Counter(entry.language for entry in resolved_entries).items(),
                key=lambda item: (-item[1], item[0]),
            )
        )
        build_systems, test_commands = _detect_build_configuration(known_paths)
        manifest_payload = {
            "schema_version": 1,
            "snapshot_hash": snapshot_hash,
            "files": [entry.to_dict() for entry in resolved_entries],
            "ignored_paths": sorted(set(ignored_paths)),
            "languages": languages,
            "build_systems": build_systems,
            "test_commands": test_commands,
            "dependencies": dependencies,
        }
        manifest_hash = _sha256_bytes(
            json.dumps(
                manifest_payload, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        )
        manifest = RepositoryManifest(
            schema_version=1,
            snapshot_hash=snapshot_hash,
            manifest_hash=manifest_hash,
            files=tuple(resolved_entries),
            ignored_paths=tuple(sorted(set(ignored_paths))),
            languages=languages,
            build_systems=build_systems,
            test_commands=test_commands,
            dependencies=dependencies,
        )
        return RepositoryIndex(root, manifest)


class RepositoryIndex:
    def __init__(self, root: Path, manifest: RepositoryManifest) -> None:
        self.root = root.resolve()
        self.manifest = manifest
        self._entries = {entry.path: entry for entry in manifest.files}

    def search_paths(self, query: str, *, limit: int = 20) -> tuple[SearchResult, ...]:
        normalized = query.casefold()
        wildcard = any(character in query for character in "*?[")
        results: list[SearchResult] = []
        for entry in self.manifest.files:
            matches = (
                fnmatch.fnmatchcase(entry.path, query)
                if wildcard
                else normalized in entry.path.casefold()
            )
            if matches:
                results.append(
                    SearchResult(
                        self.manifest.snapshot_hash,
                        entry.path,
                        1,
                        1,
                        "path",
                        entry.path,
                        1.0 if entry.path.casefold() == normalized else 0.8,
                    )
                )
            if len(results) >= limit:
                break
        return tuple(results)

    def search_text(
        self,
        query: str,
        *,
        path_glob: str | None = None,
        limit: int = 20,
    ) -> tuple[SearchResult, ...]:
        if not query:
            return ()
        needle = query.casefold()
        results: list[SearchResult] = []
        for entry in self.manifest.files:
            if not entry.indexed:
                continue
            if path_glob and not fnmatch.fnmatchcase(entry.path, path_glob):
                continue
            content = self._read_verified(entry)
            lines = content.splitlines()
            for line_number, line in enumerate(lines, start=1):
                if needle not in line.casefold():
                    continue
                start = max(1, line_number - 1)
                end = min(len(lines), line_number + 1)
                results.append(
                    SearchResult(
                        self.manifest.snapshot_hash,
                        entry.path,
                        start,
                        end,
                        "text",
                        "\n".join(lines[start - 1 : end]),
                    )
                )
                if len(results) >= limit:
                    return tuple(results)
        return tuple(results)

    def search_symbols(
        self, query: str, *, limit: int = 20
    ) -> tuple[SearchResult, ...]:
        needle = query.casefold()
        results: list[SearchResult] = []
        for entry in self.manifest.files:
            if not entry.indexed:
                continue
            for symbol in entry.symbols:
                if needle not in symbol.name.casefold():
                    continue
                content = self._read_verified(entry)
                lines = content.splitlines()
                end = min(len(lines), symbol.line + 2)
                results.append(
                    SearchResult(
                        self.manifest.snapshot_hash,
                        entry.path,
                        symbol.line,
                        end,
                        "symbol",
                        "\n".join(lines[symbol.line - 1 : end]),
                        1.0 if symbol.name.casefold() == needle else 0.9,
                    )
                )
                if len(results) >= limit:
                    return tuple(results)
        return tuple(results)

    def dependency_neighborhood(
        self, path: str, *, max_depth: int = 1, limit: int = 20
    ) -> tuple[SearchResult, ...]:
        if path not in self._entries:
            raise KeyError(path)
        if max_depth < 1:
            raise ValueError("max_depth must be at least one")
        incoming: dict[str, set[str]] = {key: set() for key in self._entries}
        for source, targets in self.manifest.dependencies.items():
            for target in targets:
                incoming.setdefault(target, set()).add(source)
        visited = {path}
        queue = deque([(path, 0)])
        results: list[SearchResult] = []
        while queue and len(results) < limit:
            current, depth = queue.popleft()
            if depth >= max_depth:
                continue
            neighbors = set(self.manifest.dependencies.get(current, ()))
            neighbors.update(incoming.get(current, set()))
            for neighbor in sorted(neighbors):
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                queue.append((neighbor, depth + 1))
                entry = self._entries[neighbor]
                snippet = neighbor
                end_line = 1
                if entry.indexed:
                    lines = self._read_verified(entry).splitlines()
                    snippet = "\n".join(lines[: min(3, len(lines))])
                    end_line = max(1, min(3, len(lines)))
                results.append(
                    SearchResult(
                        self.manifest.snapshot_hash,
                        neighbor,
                        1,
                        end_line,
                        "dependency",
                        snippet,
                    )
                )
                if len(results) >= limit:
                    break
        return tuple(results)

    def rank_relevant_files(
        self, objective: str, *, limit: int = 10
    ) -> tuple[SearchResult, ...]:
        objective_tokens = _tokens(objective)
        if not objective_tokens:
            return ()
        ranked: list[SearchResult] = []
        for entry in self.manifest.files:
            if not entry.indexed:
                continue
            content = self._read_verified(entry)
            lines = content.splitlines()
            path_overlap = objective_tokens & _tokens(entry.path)
            symbol_overlap = objective_tokens & {
                token
                for symbol in entry.symbols
                for token in _tokens(symbol.name)
            }
            best_line = 1
            best_line_overlap: set[str] = set()
            total_content_matches = 0
            for line_number, line in enumerate(lines, start=1):
                overlap = objective_tokens & _tokens(line)
                total_content_matches += len(overlap)
                if len(overlap) > len(best_line_overlap):
                    best_line = line_number
                    best_line_overlap = overlap
            raw_score = (
                len(path_overlap) * 4
                + len(symbol_overlap) * 5
                + len(best_line_overlap) * 3
                + min(total_content_matches, 8)
            )
            if raw_score == 0:
                continue
            test_path = any(
                part in {"test", "tests", "spec", "specs"}
                or part.startswith("test_")
                or part.endswith((".test.ts", ".spec.ts"))
                for part in entry.path.casefold().split("/")
            )
            score = float(raw_score) * (0.55 if test_path else 1.0)
            start = max(1, best_line - 2)
            end = min(len(lines), best_line + 2)
            ranked.append(
                SearchResult(
                    self.manifest.snapshot_hash,
                    entry.path,
                    start,
                    max(start, end),
                    "relevance",
                    "\n".join(lines[start - 1 : end]),
                    score,
                )
            )
        ranked.sort(key=lambda item: (-item.score, item.path, item.start_line))
        return tuple(ranked[:limit])

    def search(
        self,
        query: str,
        *,
        modes: Sequence[str] = ("path", "text", "symbol"),
        limit: int = 20,
    ) -> tuple[SearchResult, ...]:
        results: list[SearchResult] = []
        seen: set[tuple[str, int, int, str]] = set()
        searches = {
            "path": self.search_paths,
            "text": self.search_text,
            "symbol": self.search_symbols,
        }
        for mode in modes:
            if mode not in searches:
                raise ValueError(f"unsupported search mode: {mode}")
            for result in searches[mode](query, limit=limit):
                key = (
                    result.path,
                    result.start_line,
                    result.end_line,
                    result.kind,
                )
                if key not in seen:
                    seen.add(key)
                    results.append(result)
                if len(results) >= limit:
                    return tuple(results)
        return tuple(results)

    def build_context_pack(
        self,
        results: Iterable[SearchResult],
        *,
        budget_characters: int,
    ) -> ContextPack:
        if budget_characters <= 0:
            raise ValueError("budget_characters must be positive")
        items: list[ContextItem] = []
        used = 0
        truncated = False
        seen: set[str] = set()
        candidates = list(results)
        for result in candidates:
            if result.snapshot_hash != self.manifest.snapshot_hash:
                raise SnapshotMismatchError(
                    "search result belongs to a different snapshot"
                )
            if result.provenance in seen:
                continue
            seen.add(result.provenance)
            header = f"[{result.provenance}]\n"
            content = f"{header}{result.snippet}\n"
            if used + len(content) > budget_characters:
                truncated = True
                continue
            items.append(ContextItem(result.provenance, content, len(content)))
            used += len(content)
        return ContextPack(
            self.manifest.snapshot_hash,
            budget_characters,
            used,
            truncated,
            tuple(items),
        )

    def _read_verified(self, entry: FileEntry) -> str:
        if not entry.indexed:
            raise ValueError(
                f"{entry.path} is excluded from retrieval: "
                f"{entry.exclusion_reason}"
            )
        path = (self.root / entry.path).resolve()
        if self.root not in path.parents:
            raise ValueError("manifest path escapes repository root")
        content = path.read_bytes()
        if _sha256_bytes(content) != entry.sha256:
            raise SnapshotMismatchError(
                f"content changed after indexing: {entry.path}"
            )
        return content.decode("utf-8")
