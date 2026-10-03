from __future__ import annotations

import base64
import hashlib
import re
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from .github import (
    GitHubTransport,
    PublisherConflictError,
    PublisherPermissionError,
    PublisherTransientError,
    _error_message,
    _nested_string,
    _q,
)


MAX_INSTALLATIONS = 1_000
MAX_REPOSITORIES = 1_000
MAX_FILES = 500
MAX_FILE_BYTES = 2_000_000
MAX_TOTAL_BYTES = 20_000_000
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
_RESERVED_WINDOWS_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}


class GitHubReadTokenProvider(Protocol):
    def app_token(self) -> str: ...

    def read_token_for(self, installation_id: int) -> str: ...


@dataclass(frozen=True)
class InstallationSummary:
    installation_id: int
    account_login: str
    account_type: str
    repository_selection: str

    def to_dict(self) -> dict[str, str | int]:
        return {
            "installation_id": self.installation_id,
            "account_login": self.account_login,
            "account_type": self.account_type,
            "repository_selection": self.repository_selection,
        }


@dataclass(frozen=True)
class RepositorySummary:
    owner: str
    name: str
    full_name: str
    default_branch: str
    private: bool

    def to_dict(self) -> dict[str, str | bool]:
        return {
            "owner": self.owner,
            "name": self.name,
            "full_name": self.full_name,
            "default_branch": self.default_branch,
            "private": self.private,
        }


@dataclass(frozen=True)
class MaterializedRepository:
    installation_id: int
    owner: str
    name: str
    base_ref: str
    base_sha: str
    tree_sha: str
    file_count: int
    total_bytes: int
    path: Path


class GitHubCatalog:
    """Read-only discovery and immutable Git-object materialization boundary."""

    def __init__(
        self,
        tokens: GitHubReadTokenProvider,
        transport: GitHubTransport,
    ) -> None:
        self.tokens = tokens
        self.transport = transport

    def list_installations(self) -> tuple[InstallationSummary, ...]:
        values = self._paged_list(
            "/app/installations",
            self.tokens.app_token(),
            None,
            MAX_INSTALLATIONS,
        )
        installations: list[InstallationSummary] = []
        for item in values:
            if not isinstance(item, dict) or not isinstance(item.get("account"), dict):
                raise PublisherTransientError("GitHub installation has invalid shape")
            installation_id = item.get("id")
            account = item["account"]
            login = account.get("login")
            account_type = account.get("type")
            selection = item.get("repository_selection")
            if (
                not isinstance(installation_id, int)
                or installation_id <= 0
                or not isinstance(login, str)
                or not isinstance(account_type, str)
                or selection not in {"all", "selected"}
            ):
                raise PublisherTransientError("GitHub installation is incomplete")
            if item.get("suspended_at") is not None:
                continue
            installations.append(
                InstallationSummary(
                    installation_id, login, account_type, str(selection)
                )
            )
        return tuple(installations)

    def list_repositories(
        self, installation_id: int
    ) -> tuple[RepositorySummary, ...]:
        values = self._paged_list(
            "/installation/repositories",
            self.tokens.read_token_for(installation_id),
            "repositories",
            MAX_REPOSITORIES,
        )
        repositories: list[RepositorySummary] = []
        for item in values:
            if not isinstance(item, dict) or not isinstance(item.get("owner"), dict):
                raise PublisherTransientError("GitHub repository has invalid shape")
            owner = item["owner"].get("login")
            name = item.get("name")
            full_name = item.get("full_name")
            default_branch = item.get("default_branch")
            if not all(
                isinstance(value, str) and value
                for value in (owner, name, full_name, default_branch)
            ):
                raise PublisherTransientError("GitHub repository is incomplete")
            if item.get("archived") is True or item.get("disabled") is True:
                continue
            repositories.append(
                RepositorySummary(
                    owner,
                    name,
                    full_name,
                    default_branch,
                    item.get("private") is True,
                )
            )
        return tuple(repositories)

    def materialize_selected_repository(
        self,
        installation_id: int,
        owner: str,
        name: str,
        base_ref: str,
        destination: Path,
    ) -> MaterializedRepository:
        if not _REF.fullmatch(base_ref) or any(
            marker in base_ref for marker in ("..", "//")
        ):
            raise ValueError("base_ref is invalid")
        repositories = self.list_repositories(installation_id)
        selected = next(
            (
                repository
                for repository in repositories
                if repository.owner.casefold() == owner.casefold()
                and repository.name.casefold() == name.casefold()
            ),
            None,
        )
        if selected is None:
            raise PublisherPermissionError(
                "repository is not accessible to the selected installation"
            )
        token = self.tokens.read_token_for(installation_id)
        prefix = f"/repos/{_q(selected.owner)}/{_q(selected.name)}"
        reference = self._json(
            "GET",
            f"{prefix}/git/ref/heads/{_q(base_ref)}",
            token,
        )
        base_sha = _nested_string(reference, "object", "sha")
        commit = self._json(
            "GET", f"{prefix}/git/commits/{_q(base_sha)}", token
        )
        tree_sha = _nested_string(commit, "tree", "sha")
        tree = self._json(
            "GET",
            f"{prefix}/git/trees/{_q(tree_sha)}",
            token,
            query={"recursive": "1"},
        )
        entries = self._validated_tree(tree)
        destination.mkdir(parents=True, exist_ok=False)
        try:
            for path, mode, size, blob_sha in entries:
                blob = self._json(
                    "GET", f"{prefix}/git/blobs/{_q(blob_sha)}", token
                )
                content = _decode_blob(blob, blob_sha, size)
                target = destination.joinpath(*PurePosixPath(path).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                if mode == "100755":
                    target.chmod(target.stat().st_mode | 0o111)
        except Exception:
            shutil.rmtree(destination, ignore_errors=True)
            raise
        return MaterializedRepository(
            installation_id,
            selected.owner,
            selected.name,
            base_ref,
            base_sha,
            tree_sha,
            len(entries),
            sum(item[2] for item in entries),
            destination,
        )

    def _validated_tree(
        self, tree: Any
    ) -> tuple[tuple[str, str, int, str], ...]:
        if not isinstance(tree, dict) or tree.get("truncated") is not False:
            raise PublisherConflictError("repository tree is missing or truncated")
        raw_entries = tree.get("tree")
        if not isinstance(raw_entries, list):
            raise PublisherTransientError("GitHub tree has invalid shape")
        entries: list[tuple[str, str, int, str]] = []
        portable_paths: set[str] = set()
        total_bytes = 0
        for item in raw_entries:
            if not isinstance(item, dict):
                raise PublisherTransientError("GitHub tree entry is invalid")
            entry_type = item.get("type")
            if entry_type == "tree":
                continue
            if entry_type != "blob" or item.get("mode") not in {"100644", "100755"}:
                raise PublisherConflictError(
                    "repository contains unsupported symlinks or submodules"
                )
            path = item.get("path")
            size = item.get("size")
            sha = item.get("sha")
            if (
                not isinstance(path, str)
                or not isinstance(size, int)
                or not isinstance(sha, str)
            ):
                raise PublisherTransientError("GitHub blob entry is incomplete")
            _validate_repository_path(path)
            portable = path.casefold()
            if portable in portable_paths:
                raise PublisherConflictError("repository contains case-colliding paths")
            portable_paths.add(portable)
            if size < 0 or size > MAX_FILE_BYTES:
                raise PublisherConflictError("repository file exceeds size limit")
            total_bytes += size
            if total_bytes > MAX_TOTAL_BYTES:
                raise PublisherConflictError("repository exceeds total size limit")
            entries.append((path, str(item["mode"]), size, sha))
            if len(entries) > MAX_FILES:
                raise PublisherConflictError("repository exceeds file-count limit")
        return tuple(entries)

    def _paged_list(
        self,
        path: str,
        token: str,
        collection_key: str | None,
        maximum: int,
    ) -> list[Any]:
        values: list[Any] = []
        for page in range(1, maximum // 100 + 2):
            data = self._json(
                "GET", path, token, query={"per_page": "100", "page": str(page)}
            )
            page_values = (
                data
                if collection_key is None
                else data.get(collection_key)
                if isinstance(data, dict)
                else None
            )
            if not isinstance(page_values, list):
                raise PublisherTransientError("GitHub list response has invalid shape")
            if len(values) + len(page_values) > maximum:
                raise PublisherConflictError("GitHub catalog exceeds configured limit")
            values.extend(page_values)
            if len(page_values) < 100:
                return values
        raise PublisherConflictError("GitHub pagination exceeds configured limit")

    def _json(
        self,
        method: str,
        path: str,
        token: str,
        *,
        query: dict[str, str] | None = None,
    ) -> Any:
        response = self.transport.request(method, path, token, query=query)
        if response.status == 200:
            return response.data
        message = _error_message(response.data)
        if response.status == 429 or response.status >= 500:
            raise PublisherTransientError(message)
        if response.status in {401, 403, 404}:
            raise PublisherPermissionError(message)
        raise PublisherConflictError(
            f"GitHub API returned {response.status}: {message}"
        )


def _validate_repository_path(path: str) -> None:
    if len(path) > 1_000 or "\\" in path or ":" in path:
        raise PublisherConflictError("repository contains an unsafe path")
    pure = PurePosixPath(path)
    parts = pure.parts
    if not parts or pure.is_absolute() or any(
        part in {"", ".", ".."} for part in parts
    ):
        raise PublisherConflictError("repository contains an unsafe path")
    for part in parts:
        if (
            len(part) > 255
            or part.endswith((" ", "."))
            or any(ord(character) < 32 for character in part)
            or part.casefold() == ".git"
            or part.split(".", 1)[0].casefold() in _RESERVED_WINDOWS_NAMES
        ):
            raise PublisherConflictError("repository contains an unsafe path")


def _decode_blob(value: Any, expected_sha: str, expected_size: int) -> bytes:
    if not isinstance(value, dict) or value.get("encoding") != "base64":
        raise PublisherTransientError("GitHub blob has invalid encoding")
    content = value.get("content")
    if (
        not isinstance(content, str)
        or value.get("sha") != expected_sha
        or value.get("size") != expected_size
    ):
        raise PublisherTransientError("GitHub blob evidence does not match tree")
    try:
        # GitHub wraps Base64 responses with ASCII whitespace. Normalize only
        # that formatting; strict decoding still rejects other invalid bytes.
        normalized = content.translate(str.maketrans("", "", " \t\r\n"))
        decoded = base64.b64decode(normalized, validate=True)
    except (ValueError, TypeError) as error:
        raise PublisherTransientError("GitHub blob base64 is invalid") from error
    if len(decoded) != expected_size:
        raise PublisherTransientError("GitHub blob size does not match tree")
    header = f"blob {len(decoded)}\0".encode("ascii")
    digest = (
        hashlib.sha1(header + decoded).hexdigest()
        if len(expected_sha) == 40
        else hashlib.sha256(header + decoded).hexdigest()
    )
    if digest != expected_sha:
        raise PublisherTransientError("GitHub blob hash validation failed")
    return decoded
