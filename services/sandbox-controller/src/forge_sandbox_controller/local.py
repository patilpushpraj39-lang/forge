from __future__ import annotations

import difflib
import hashlib
import io
import os
import signal
import shutil
import stat
import subprocess
import tarfile
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from forge_repo_intelligence import (
    ContextPack,
    RepositoryIndex,
    RepositoryIndexer,
    RepositoryManifest,
    SearchResult,
)

from .artifacts import LocalArtifactStore
from .protocol import (
    AppliedPatch,
    ArtifactStore,
    ArtifactRef,
    CommandResult,
    CommandStatus,
    InvalidPatchError,
    PatchArtifact,
    SandboxHandle,
    SandboxNotFoundError,
)


SNAPSHOT_MEDIA_TYPE = "application/vnd.forge.snapshot+tar"
PATCH_MEDIA_TYPE = "text/x-diff; charset=utf-8"
MAX_PATCH_BYTES = 256_000


def _snapshot_ignore(directory: str, names: list[str]) -> set[str]:
    ignored_names = {
        ".git",
        ".next",
        ".state",
        ".venv",
        ".artifacts",
        ".sandboxes",
        "__pycache__",
        "dist",
        "node_modules",
    }
    root = Path(directory)
    return {
        name
        for name in names
        if name in ignored_names or (root / name).is_symlink()
    }


def _snapshot_repository(source: Path, destination: Path) -> tuple[str, int]:
    shutil.copytree(source, destination, ignore=_snapshot_ignore)
    return _hash_repository(destination)


def _hash_repository(repository: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    file_count = 0
    for path in sorted(item for item in repository.rglob("*") if item.is_file()):
        if path.is_symlink():
            raise ValueError("repository snapshots cannot contain symlinks")
        relative = path.relative_to(repository).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        digest.update(b"\0")
        file_count += 1
    return digest.hexdigest(), file_count


def _write_snapshot_archive(source: Path, destination: Path) -> None:
    with tarfile.open(destination, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for path in sorted(item for item in source.rglob("*") if item.is_file()):
            if path.is_symlink():
                raise ValueError("snapshot archives cannot contain symlinks")
            relative = path.relative_to(source).as_posix()
            information = tarfile.TarInfo(relative)
            information.size = path.stat().st_size
            information.mode = (
                0o755 if path.stat().st_mode & stat.S_IXUSR else 0o644
            )
            information.mtime = 0
            information.uid = 0
            information.gid = 0
            information.uname = ""
            information.gname = ""
            with path.open("rb") as content:
                archive.addfile(information, content)


def _extract_snapshot_archive(content: bytes, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    with tarfile.open(fileobj=io.BytesIO(content), mode="r:") as archive:
        for member in archive.getmembers():
            relative = PurePosixPath(member.name)
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or not member.isfile()
            ):
                raise ValueError("snapshot contains an unsafe archive member")
            output = destination.joinpath(*relative.parts)
            output.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise ValueError("snapshot member has no content")
            with source, output.open("wb") as target:
                shutil.copyfileobj(source, target)
            output.chmod(member.mode & 0o755)


def _repository_files(root: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise InvalidPatchError("workspace diffs cannot contain symlinks")
        if path.is_file():
            files[path.relative_to(root).as_posix()] = path
    return files


def _decode_patchable(path: Path | None) -> list[str]:
    if path is None:
        return []
    content = path.read_bytes()
    if b"\0" in content:
        raise InvalidPatchError("binary changes are not supported")
    try:
        return content.decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError as error:
        raise InvalidPatchError("non-UTF-8 changes are not supported") from error


def _build_unified_diff(base: Path, workspace: Path) -> tuple[bytes, tuple[str, ...]]:
    before = _repository_files(base)
    after = _repository_files(workspace)
    changed_paths: list[str] = []
    chunks: list[str] = []
    for relative in sorted(set(before) | set(after)):
        old_path = before.get(relative)
        new_path = after.get(relative)
        if old_path is not None and new_path is not None:
            if _hash_file(old_path) == _hash_file(new_path):
                continue
        changed_paths.append(relative)
        old_lines = _decode_patchable(old_path)
        new_lines = _decode_patchable(new_path)
        from_file = f"a/{relative}" if old_path is not None else "/dev/null"
        to_file = f"b/{relative}" if new_path is not None else "/dev/null"
        chunks.append(f"diff --git a/{relative} b/{relative}\n")
        if old_path is None:
            chunks.append("new file mode 100644\n")
        elif new_path is None:
            chunks.append("deleted file mode 100644\n")
        chunks.extend(
            difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile=from_file,
                tofile=to_file,
                lineterm="\n",
            )
        )
    return "".join(chunks).encode("utf-8"), tuple(changed_paths)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_patch(patch: bytes) -> tuple[str, ...]:
    if not patch or len(patch) > MAX_PATCH_BYTES:
        raise InvalidPatchError("patch is empty or exceeds the byte limit")
    try:
        text = patch.decode("utf-8")
    except UnicodeDecodeError as error:
        raise InvalidPatchError("patch must be UTF-8 text") from error
    if "GIT binary patch" in text or "120000" in text:
        raise InvalidPatchError("binary and symlink patches are not supported")
    paths: set[str] = set()
    for line in text.splitlines():
        if not line.startswith(("--- ", "+++ ")):
            continue
        value = line[4:].split("\t", 1)[0]
        if value == "/dev/null":
            continue
        if not value.startswith(("a/", "b/")):
            raise InvalidPatchError("patch paths must use a/ and b/ prefixes")
        relative = PurePosixPath(value[2:])
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not relative.parts
            or relative.parts[0] == ".git"
        ):
            raise InvalidPatchError("patch path escapes the repository")
        paths.add(relative.as_posix())
    if not paths:
        raise InvalidPatchError("patch does not contain file headers")
    return tuple(sorted(paths))


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait(timeout=1)


class _OutputCollector:
    def __init__(self, stream: object, limit_bytes: int) -> None:
        self._stream = stream
        self._limit_bytes = limit_bytes
        self._content = bytearray()
        self.exceeded = threading.Event()
        self._thread = threading.Thread(target=self._read, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def finish(self) -> str:
        self._thread.join(timeout=2)
        if self._thread.is_alive():
            raise RuntimeError("command output reader did not terminate")
        return bytes(self._content).decode("utf-8", errors="replace")

    def _read(self) -> None:
        stream = self._stream
        while True:
            block = stream.read(8192)  # type: ignore[attr-defined]
            if not block:
                return
            remaining = self._limit_bytes - len(self._content)
            if remaining > 0:
                self._content.extend(block[:remaining])
            if len(block) > remaining:
                self.exceeded.set()


def _run_bounded_process(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout_seconds: float,
    should_cancel: Callable[[], bool],
    heartbeat: Callable[[], None],
    heartbeat_interval_seconds: float,
    output_limit_bytes: int,
    forced_stop: Callable[[], None] | None = None,
    resource_exceeded: Callable[[], bool] | None = None,
) -> CommandResult:
    process_options: dict[str, object] = {}
    if os.name == "nt":
        process_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        process_options["start_new_session"] = True
    process = subprocess.Popen(
        list(command),
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        **process_options,
    )
    assert process.stdout is not None
    collector = _OutputCollector(process.stdout, output_limit_bytes)
    collector.start()
    started_at = time.monotonic()
    next_heartbeat = started_at + heartbeat_interval_seconds

    def stop() -> None:
        if forced_stop is not None:
            forced_stop()
        _terminate_process(process)

    while process.poll() is None:
        if should_cancel():
            stop()
            return CommandResult(
                CommandStatus.CANCELLED,
                None,
                collector.finish(),
                collector.exceeded.is_set(),
            )
        if collector.exceeded.is_set():
            stop()
            return CommandResult(
                CommandStatus.OUTPUT_LIMIT, None, collector.finish(), True
            )
        if resource_exceeded is not None and resource_exceeded():
            stop()
            return CommandResult(
                CommandStatus.RESOURCE_LIMIT,
                None,
                collector.finish(),
                collector.exceeded.is_set(),
            )
        now = time.monotonic()
        if now - started_at >= timeout_seconds:
            stop()
            return CommandResult(
                CommandStatus.TIMED_OUT,
                None,
                collector.finish(),
                collector.exceeded.is_set(),
            )
        if now >= next_heartbeat:
            heartbeat()
            next_heartbeat = now + heartbeat_interval_seconds
        time.sleep(0.05)

    if os.name != "nt":
        _terminate_process(process)
    output = collector.finish()
    if collector.exceeded.is_set():
        return CommandResult(CommandStatus.OUTPUT_LIMIT, None, output, True)
    status = (
        CommandStatus.COMPLETED
        if process.returncode == 0
        else CommandStatus.FAILED
    )
    return CommandResult(status, process.returncode, output)


@dataclass
class _SandboxState:
    root: Path
    base: Path
    workspace: Path
    snapshot_hash: str
    snapshot_artifact: ArtifactRef
    repository_index: RepositoryIndex | None = None


class LocalSandboxController:
    """Disposable local backend for the sandbox-controller protocol.

    This adapter narrows the worker interface and protects the source checkout,
    but it does not provide host security isolation. A container-backed service
    will implement the same protocol in Milestone 3.
    """

    def __init__(self, artifact_store: ArtifactStore | None = None) -> None:
        self._sandboxes: dict[str, _SandboxState] = {}
        self._indexer = RepositoryIndexer()
        self._artifact_store = artifact_store or LocalArtifactStore()

    def create(self, repository_path: Path) -> SandboxHandle:
        if not repository_path.is_dir():
            raise FileNotFoundError(repository_path)
        sandbox_id = str(uuid.uuid4())
        root = Path(tempfile.mkdtemp(prefix=f"forge-{sandbox_id}-"))
        base = root / "base"
        workspace = root / "repository"
        try:
            snapshot_hash, file_count = _snapshot_repository(
                repository_path, base
            )
            shutil.copytree(base, workspace)
            archive = root / "snapshot.tar"
            _write_snapshot_archive(base, archive)
            snapshot_artifact = self._artifact_store.put_file(
                archive, SNAPSHOT_MEDIA_TYPE
            )
            archive.unlink()
        except Exception:
            shutil.rmtree(root, ignore_errors=True)
            raise
        self._sandboxes[sandbox_id] = _SandboxState(
            root, base, workspace, snapshot_hash, snapshot_artifact
        )
        return SandboxHandle(
            sandbox_id,
            snapshot_hash,
            file_count,
            snapshot_artifact,
        )

    def create_from_snapshot(self, snapshot: ArtifactRef) -> SandboxHandle:
        if snapshot.media_type != SNAPSHOT_MEDIA_TYPE:
            raise ValueError("artifact is not a Forge repository snapshot")
        sandbox_id = str(uuid.uuid4())
        root = Path(tempfile.mkdtemp(prefix=f"forge-{sandbox_id}-"))
        base = root / "base"
        workspace = root / "repository"
        try:
            content = self._artifact_store.read_bytes(
                snapshot, max_bytes=snapshot.size_bytes
            )
            _extract_snapshot_archive(content, base)
            snapshot_hash, file_count = _hash_repository(base)
            shutil.copytree(base, workspace)
        except Exception:
            shutil.rmtree(root, ignore_errors=True)
            raise
        self._sandboxes[sandbox_id] = _SandboxState(
            root, base, workspace, snapshot_hash, snapshot
        )
        return SandboxHandle(sandbox_id, snapshot_hash, file_count, snapshot)

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

    def apply_patch(
        self,
        sandbox_id: str,
        patch: bytes,
        expected_sha256: str | None = None,
    ) -> AppliedPatch:
        changed_paths = _validate_patch(patch)
        patch_hash = hashlib.sha256(patch).hexdigest()
        if expected_sha256 is not None and patch_hash != expected_sha256:
            raise InvalidPatchError("patch checksum does not match expectation")
        state = self._require_sandbox(sandbox_id)
        check = subprocess.run(
            ["git", "apply", "--check", "--recount", "-"],
            cwd=state.workspace,
            input=patch,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
            check=False,
        )
        if check.returncode != 0:
            raise InvalidPatchError(
                check.stdout.decode("utf-8", errors="replace")[:2048]
            )
        applied = subprocess.run(
            ["git", "apply", "--recount", "-"],
            cwd=state.workspace,
            input=patch,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
            check=False,
        )
        if applied.returncode != 0:
            raise InvalidPatchError(
                applied.stdout.decode("utf-8", errors="replace")[:2048]
            )
        state.repository_index = None
        return AppliedPatch(patch_hash, changed_paths)

    def diff(self, sandbox_id: str) -> PatchArtifact:
        state = self._require_sandbox(sandbox_id)
        patch, changed_paths = _build_unified_diff(state.base, state.workspace)
        artifact = self._artifact_store.put_bytes(patch, PATCH_MEDIA_TYPE)
        return PatchArtifact(artifact, changed_paths)

    def read_artifact(self, reference: ArtifactRef, max_bytes: int) -> bytes:
        return self._artifact_store.read_bytes(reference, max_bytes)

    def execute(
        self,
        sandbox_id: str,
        command: Sequence[str],
        timeout_seconds: float,
        should_cancel: Callable[[], bool],
        heartbeat: Callable[[], None],
        heartbeat_interval_seconds: float,
        output_limit_bytes: int = 4096,
    ) -> CommandResult:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds must be positive")
        if output_limit_bytes <= 0:
            raise ValueError("output_limit_bytes must be positive")
        workspace = self._require_sandbox(sandbox_id).workspace
        return _run_bounded_process(
            command,
            cwd=workspace,
            timeout_seconds=timeout_seconds,
            should_cancel=should_cancel,
            heartbeat=heartbeat,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
            output_limit_bytes=output_limit_bytes,
        )

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
