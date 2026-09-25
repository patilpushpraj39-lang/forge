from __future__ import annotations

import os
from pathlib import Path

from .artifacts import LocalArtifactStore
from .docker import DockerSandboxController
from .local import LocalSandboxController
from .protocol import SandboxController
from .s3_artifacts import S3ArtifactStore


def _create_artifact_store():
    backend = os.environ.get("FORGE_ARTIFACT_BACKEND", "local").casefold()
    if backend == "local":
        return LocalArtifactStore(
            Path(os.environ.get("FORGE_ARTIFACT_PATH", ".state/artifacts"))
        )
    if backend == "s3":
        bucket = os.environ.get("FORGE_ARTIFACT_BUCKET")
        if not bucket:
            raise RuntimeError(
                "FORGE_ARTIFACT_BUCKET is required for S3 artifacts"
            )
        return S3ArtifactStore(
            bucket,
            prefix=os.environ.get(
                "FORGE_ARTIFACT_PREFIX", "forge/artifacts"
            ),
            endpoint_url=os.environ.get("FORGE_ARTIFACT_ENDPOINT_URL"),
            region_name=os.environ.get("FORGE_ARTIFACT_REGION"),
        )
    raise RuntimeError(f"unsupported artifact backend: {backend}")


def create_sandbox_controller() -> SandboxController:
    backend = os.environ.get("FORGE_SANDBOX_BACKEND", "local").casefold()
    artifact_store = _create_artifact_store()
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
