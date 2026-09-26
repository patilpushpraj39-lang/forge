from __future__ import annotations

import re
from dataclasses import dataclass


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
SNAPSHOT_MEDIA_TYPE = "application/vnd.forge.snapshot+tar"


@dataclass(frozen=True)
class SourceSnapshot:
    """Content-addressed repository source captured before a run is queued."""

    sha256: str
    size_bytes: int
    media_type: str = SNAPSHOT_MEDIA_TYPE

    def __post_init__(self) -> None:
        if not _SHA256.fullmatch(self.sha256):
            raise ValueError("source snapshot sha256 is invalid")
        if self.size_bytes <= 0 or self.size_bytes > 100_000_000:
            raise ValueError("source snapshot size is outside the allowed range")
        if self.media_type != SNAPSHOT_MEDIA_TYPE:
            raise ValueError("source snapshot media type is invalid")

    def to_dict(self) -> dict[str, str | int]:
        return {
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "media_type": self.media_type,
        }
