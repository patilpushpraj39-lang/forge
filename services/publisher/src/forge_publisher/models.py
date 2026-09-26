from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GitHubChange:
    path: str
    content: bytes | None
    executable: bool = False


@dataclass(frozen=True)
class PullRequestResult:
    number: int
    url: str
    head_sha: str
