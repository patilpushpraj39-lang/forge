from __future__ import annotations

import re
import subprocess
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .local import LocalSandboxController, _run_bounded_process
from .protocol import (
    ArtifactStore,
    ArtifactRef,
    CommandResult,
    CommandStatus,
    SandboxHandle,
)


IMAGE_DIGEST_PATTERN = re.compile(r"^.+@sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class ContainerPolicy:
    cpu_count: float = 1.0
    memory_megabytes: int = 256
    process_limit: int = 64
    workspace_bytes: int = 64 * 1024 * 1024
    temporary_megabytes: int = 64
    output_bytes: int = 64 * 1024
    user: str = "65532:65532"

    def __post_init__(self) -> None:
        values = (
            self.cpu_count,
            self.memory_megabytes,
            self.process_limit,
            self.workspace_bytes,
            self.temporary_megabytes,
            self.output_bytes,
        )
        if any(value <= 0 for value in values):
            raise ValueError("container policy limits must be positive")


def build_docker_run_command(
    *,
    docker_binary: str,
    image: str,
    workspace: Path,
    container_name: str,
    command: Sequence[str],
    policy: ContainerPolicy,
) -> list[str]:
    if not IMAGE_DIGEST_PATTERN.fullmatch(image):
        raise ValueError("sandbox image must be pinned by SHA-256 digest")
    workspace_text = str(workspace.resolve())
    if "," in workspace_text:
        raise ValueError("workspace path cannot contain a comma")
    memory = f"{policy.memory_megabytes}m"
    temporary = f"{policy.temporary_megabytes}m"
    return [
        docker_binary,
        "run",
        "--rm",
        "--name",
        container_name,
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges=true",
        "--pids-limit",
        str(policy.process_limit),
        "--memory",
        memory,
        "--memory-swap",
        memory,
        "--cpus",
        str(policy.cpu_count),
        "--ulimit",
        f"fsize={policy.workspace_bytes}:{policy.workspace_bytes}",
        "--ulimit",
        "nofile=256:256",
        "--ipc=none",
        "--init",
        "--user",
        policy.user,
        "--workdir",
        "/workspace",
        "--tmpfs",
        f"/tmp:rw,noexec,nosuid,nodev,size={temporary},"
        "uid=65532,gid=65532,mode=1777",
        "--mount",
        f"type=bind,src={workspace_text},dst=/workspace",
        "--env",
        "HOME=/tmp",
        "--env",
        "TMPDIR=/tmp",
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
        "--label",
        "forge.sandbox=true",
        image,
        *command,
    ]


class DockerSandboxController(LocalSandboxController):
    """Docker-backed execution with a deny-by-default runtime policy.

    Snapshots, manifests, patches, and artifacts use the same control-plane
    implementation as the local adapter. Only command execution crosses into an
    untrusted, ephemeral container. A new container is used per command so
    background processes cannot survive command completion.
    """

    def __init__(
        self,
        image: str,
        *,
        policy: ContainerPolicy | None = None,
        docker_binary: str = "docker",
        artifact_store: ArtifactStore | None = None,
    ) -> None:
        if not IMAGE_DIGEST_PATTERN.fullmatch(image):
            raise ValueError("sandbox image must be pinned by SHA-256 digest")
        super().__init__(artifact_store)
        self.image = image
        self.policy = policy or ContainerPolicy()
        self.docker_binary = docker_binary

    def create(self, repository_path: Path) -> SandboxHandle:
        handle = super().create(repository_path)
        self._make_workspace_writable(handle.sandbox_id)
        return handle

    def create_from_snapshot(self, snapshot: ArtifactRef) -> SandboxHandle:
        handle = super().create_from_snapshot(snapshot)
        self._make_workspace_writable(handle.sandbox_id)
        return handle

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
        state = self._require_sandbox(sandbox_id)
        container_name = f"forge-exec-{uuid.uuid4()}"
        docker_command = build_docker_run_command(
            docker_binary=self.docker_binary,
            image=self.image,
            workspace=state.workspace,
            container_name=container_name,
            command=command,
            policy=self.policy,
        )
        last_size_check = 0.0
        size_exceeded = False

        def workspace_exceeded(*, refresh: bool = False) -> bool:
            nonlocal last_size_check, size_exceeded
            now = time.monotonic()
            if not refresh and now - last_size_check < 0.2:
                return size_exceeded
            last_size_check = now
            size_exceeded = (
                self._workspace_size(state.workspace)
                > self.policy.workspace_bytes
            )
            return size_exceeded

        result = _run_bounded_process(
            docker_command,
            cwd=state.root,
            timeout_seconds=timeout_seconds,
            should_cancel=should_cancel,
            heartbeat=heartbeat,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
            output_limit_bytes=min(
                output_limit_bytes, self.policy.output_bytes
            ),
            forced_stop=lambda: self._stop_container(container_name),
            resource_exceeded=workspace_exceeded,
        )
        if (
            result.status in {CommandStatus.COMPLETED, CommandStatus.FAILED}
            # A fast write can finish between polling intervals.
            and workspace_exceeded(refresh=True)
        ):
            return CommandResult(
                CommandStatus.RESOURCE_LIMIT,
                None,
                result.output,
                result.output_truncated,
            )
        return result

    def _stop_container(self, container_name: str) -> None:
        subprocess.run(
            [self.docker_binary, "kill", container_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )

    def _make_workspace_writable(self, sandbox_id: str) -> None:
        workspace = self._require_sandbox(sandbox_id).workspace
        workspace.chmod(workspace.stat().st_mode | 0o777)
        for path in workspace.rglob("*"):
            if path.is_symlink():
                continue
            if path.is_dir():
                path.chmod(path.stat().st_mode | 0o777)
            else:
                path.chmod(path.stat().st_mode | 0o666)

    @staticmethod
    def _workspace_size(workspace: Path) -> int:
        return sum(
            path.stat().st_size
            for path in workspace.rglob("*")
            if path.is_file() and not path.is_symlink()
        )


def resolve_pinned_image(image_reference: str, docker_binary: str = "docker") -> str:
    """Resolve an already-pulled tag to its first immutable repository digest."""

    result = subprocess.run(
        [
            docker_binary,
            "image",
            "inspect",
            image_reference,
            "--format",
            "{{index .RepoDigests 0}}",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
        text=True,
    )
    resolved = result.stdout.strip()
    if result.returncode != 0 or not IMAGE_DIGEST_PATTERN.fullmatch(resolved):
        message = result.stderr.strip() or "image has no repository digest"
        raise RuntimeError(message)
    return resolved
