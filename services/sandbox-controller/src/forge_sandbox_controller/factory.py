from __future__ import annotations

import os
from pathlib import Path

from .artifacts import LocalArtifactStore
from .docker import DockerSandboxController
from .local import LocalSandboxController
from .protocol import SandboxController


def create_sandbox_controller() -> SandboxController:
    backend = os.environ.get("FORGE_SANDBOX_BACKEND", "local").casefold()
    artifact_store = LocalArtifactStore(
        Path(os.environ.get("FORGE_ARTIFACT_PATH", ".state/artifacts"))
    )
    if backend == "local":
        return LocalSandboxController(artifact_store)
    if backend == "docker":
        image = os.environ.get("FORGE_SANDBOX_IMAGE")
        if not image:
            raise RuntimeError(
                "FORGE_SANDBOX_IMAGE is required for the Docker backend"
            )
        return DockerSandboxController(image, artifact_store=artifact_store)
    raise RuntimeError(f"unsupported sandbox backend: {backend}")
