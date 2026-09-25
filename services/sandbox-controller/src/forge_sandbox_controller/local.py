from __future__ import annotations

import hashlib
import os
import signal
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from forge_repo_intelligence import (
    ContextPack,
    RepositoryIndex,
    RepositoryIndexer,
    RepositoryManifest,
    SearchResult,
)

from .protocol import (
    CommandResult,
    CommandStatus,
    SandboxHandle,
    SandboxNotFoundError,
)


def _snapshot_repository(source: Path, destination: Path) -> tuple[str, int]:
    ignored = shutil.ignore_patterns(
        ".git",
        ".next",
        ".state",
        ".venv",
        ".artifacts",
        ".sandboxes",
        "__pycache__",
        "dist",
        "node_modules",
    )
    shutil.copytree(source, destination, ignore=ignored)
    digest = hashlib.sha256()
    file_count = 0
    for path in sorted(item for item in destination.rglob("*") if item.is_file()):
        relative = path.relative_to(destination).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
        file_count += 1
    return digest.hexdigest(), file_count


def _terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return

    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    try:
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            process.kill()
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        process.wait(timeout=1)


@dataclass
class _SandboxState:
    root: Path
    workspace: Path
    snapshot_hash: str
    repository_index: RepositoryIndex | None = None


class LocalSandboxController:
    """Disposable local backend for the sandbox-controller protocol.

    This adapter narrows the worker interface and protects the source checkout,
    but it does not provide host security isolation. A container-backed service
    will implement the same protocol in Milestone 3.
    """

    def __init__(self) -> None:
        self._sandboxes: dict[str, _SandboxState] = {}
        self._indexer = RepositoryIndexer()

    def create(self, repository_path: Path) -> SandboxHandle:
        if not repository_path.is_dir():
            raise FileNotFoundError(repository_path)
        sandbox_id = str(uuid.uuid4())
        root = Path(tempfile.mkdtemp(prefix=f"forge-{sandbox_id}-"))
        workspace = root / "repository"
        try:
            snapshot_hash, file_count = _snapshot_repository(
                repository_path, workspace
            )
        except Exception:
            shutil.rmtree(root, ignore_errors=True)
            raise
        self._sandboxes[sandbox_id] = _SandboxState(
            root, workspace, snapshot_hash
        )
        return SandboxHandle(sandbox_id, snapshot_hash, file_count)

    def index_repository(self, sandbox_id: str) -> RepositoryManifest:
        state = self._require_sandbox(sandbox_id)
        if state.repository_index is None:
            state.repository_index = self._indexer.build(
                state.workspace, state.snapshot_hash
            )
        return state.repository_index.manifest

    def search_repository(
        self,
        sandbox_id: str,
        query: str,
        modes: Sequence[str] = ("path", "text", "symbol"),
        limit: int = 20,
    ) -> tuple[SearchResult, ...]:
        repository_index = self._repository_index(sandbox_id)
        return repository_index.search(query, modes=modes, limit=limit)

    def dependency_neighborhood(
        self,
        sandbox_id: str,
        path: str,
        max_depth: int = 1,
        limit: int = 20,
    ) -> tuple[SearchResult, ...]:
        repository_index = self._repository_index(sandbox_id)
        return repository_index.dependency_neighborhood(
            path, max_depth=max_depth, limit=limit
        )

    def rank_relevant_files(
        self,
        sandbox_id: str,
        objective: str,
        limit: int = 10,
    ) -> tuple[SearchResult, ...]:
        repository_index = self._repository_index(sandbox_id)
        return repository_index.rank_relevant_files(objective, limit=limit)

    def build_context_pack(
        self,
        sandbox_id: str,
        results: Sequence[SearchResult],
        budget_characters: int,
    ) -> ContextPack:
        repository_index = self._repository_index(sandbox_id)
        return repository_index.build_context_pack(
            results, budget_characters=budget_characters
        )

    def execute(
        self,
        sandbox_id: str,
        command: Sequence[str],
        timeout_seconds: float,
        should_cancel: Callable[[], bool],
        heartbeat: Callable[[], None],
        heartbeat_interval_seconds: float,
    ) -> CommandResult:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds must be positive")
        workspace = self._require_sandbox(sandbox_id).workspace
        process_options: dict[str, object] = {}
        if os.name == "nt":
            process_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            process_options["start_new_session"] = True
        process = subprocess.Popen(
            list(command),
            cwd=workspace,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            **process_options,
        )
        started_at = time.monotonic()
        next_heartbeat = started_at + heartbeat_interval_seconds

        while process.poll() is None:
            if should_cancel():
                _terminate_process(process)
                output, _ = process.communicate(timeout=1)
                return CommandResult(CommandStatus.CANCELLED, None, output[:4096])

            now = time.monotonic()
            if now - started_at >= timeout_seconds:
                _terminate_process(process)
                output, _ = process.communicate(timeout=1)
                return CommandResult(CommandStatus.TIMED_OUT, None, output[:4096])

            if now >= next_heartbeat:
                heartbeat()
                next_heartbeat = now + heartbeat_interval_seconds
            time.sleep(0.05)

        output, _ = process.communicate(timeout=1)
        status = (
            CommandStatus.COMPLETED
            if process.returncode == 0
            else CommandStatus.FAILED
        )
        return CommandResult(status, process.returncode, output[:4096])

    def destroy(self, sandbox_id: str) -> None:
        root = self._require_sandbox(sandbox_id).root
        del self._sandboxes[sandbox_id]
        shutil.rmtree(root)

    def _repository_index(self, sandbox_id: str) -> RepositoryIndex:
        self.index_repository(sandbox_id)
        repository_index = self._require_sandbox(sandbox_id).repository_index
        assert repository_index is not None
        return repository_index

    def _require_sandbox(self, sandbox_id: str) -> _SandboxState:
        try:
            return self._sandboxes[sandbox_id]
        except KeyError as error:
            raise SandboxNotFoundError(sandbox_id) from error
