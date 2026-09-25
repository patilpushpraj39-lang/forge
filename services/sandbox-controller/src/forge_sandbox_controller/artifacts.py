from __future__ import annotations

import hashlib
import shutil
import tempfile
from pathlib import Path

from .protocol import ArtifactNotFoundError, ArtifactRef


class LocalArtifactStore:
    """Content-addressed local artifact adapter.

    The artifact root is deliberately separate from disposable sandbox roots so
    snapshots and patches remain readable after a sandbox is destroyed.
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = (
            root.resolve()
            if root is not None
            else Path(tempfile.mkdtemp(prefix="forge-artifacts-")).resolve()
        )
        self.root.mkdir(parents=True, exist_ok=True)

    def put_bytes(self, content: bytes, media_type: str) -> ArtifactRef:
        digest = hashlib.sha256(content).hexdigest()
        destination = self._path_for(digest)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            temporary = destination.with_suffix(".tmp")
            temporary.write_bytes(content)
            temporary.replace(destination)
        return ArtifactRef(digest, len(content), media_type)

    def put_file(self, source: Path, media_type: str) -> ArtifactRef:
        digest = hashlib.sha256()
        size = 0
        with source.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
                size += len(block)
        checksum = digest.hexdigest()
        destination = self._path_for(checksum)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            temporary = destination.with_suffix(".tmp")
            shutil.copyfile(source, temporary)
            temporary.replace(destination)
        return ArtifactRef(checksum, size, media_type)

    def read_bytes(self, reference: ArtifactRef, max_bytes: int) -> bytes:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        if reference.size_bytes > max_bytes:
            raise ValueError("artifact exceeds read limit")
        path = self._path_for(reference.sha256)
        if not path.is_file():
            raise ArtifactNotFoundError(reference.sha256)
        content = path.read_bytes()
        if len(content) != reference.size_bytes:
            raise ArtifactNotFoundError("artifact size does not match reference")
        if hashlib.sha256(content).hexdigest() != reference.sha256:
            raise ArtifactNotFoundError("artifact checksum does not match reference")
        return content

    def _path_for(self, checksum: str) -> Path:
        if len(checksum) != 64 or any(
            character not in "0123456789abcdef" for character in checksum
        ):
            raise ValueError("invalid SHA-256 checksum")
        return self.root / "sha256" / checksum[:2] / checksum
