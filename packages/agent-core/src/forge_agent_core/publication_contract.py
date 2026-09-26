from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlparse


SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
GIT_COMMIT_PATTERN = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
REPOSITORY_PART_PATTERN = re.compile(r"[A-Za-z0-9_.-]{1,100}")
GIT_REF_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")
IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}")
IDEMPOTENCY_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{15,127}")


class ApprovalAction(StrEnum):
    CREATE_PULL_REQUEST = "create_pull_request"


class ApprovalError(RuntimeError):
    pass


class ApprovalEvidenceMismatchError(ApprovalError):
    pass


class ApprovalExpiredError(ApprovalError):
    pass


class PublicationError(RuntimeError):
    pass


class PublicationConflictError(PublicationError):
    pass


@dataclass(frozen=True)
class RepositoryTarget:
    owner: str
    name: str
    installation_id: int
    base_ref: str
    base_sha: str

    def __post_init__(self) -> None:
        if not REPOSITORY_PART_PATTERN.fullmatch(self.owner):
            raise ValueError("invalid GitHub repository owner")
        if not REPOSITORY_PART_PATTERN.fullmatch(self.name):
            raise ValueError("invalid GitHub repository name")
        if (
            not isinstance(self.installation_id, int)
            or isinstance(self.installation_id, bool)
            or self.installation_id <= 0
        ):
            raise ValueError("installation_id must be a positive integer")
        if not GIT_COMMIT_PATTERN.fullmatch(self.base_sha):
            raise ValueError("base_sha must be a full Git commit hash")
        if (
            not GIT_REF_PATTERN.fullmatch(self.base_ref)
            or ".." in self.base_ref
            or "//" in self.base_ref
            or self.base_ref.endswith(("/", ".", ".lock"))
        ):
            raise ValueError("base_ref is invalid")

    def to_dict(self) -> dict[str, str | int]:
        return {
            "owner": self.owner,
            "name": self.name,
            "installation_id": self.installation_id,
            "base_ref": self.base_ref,
            "base_sha": self.base_sha,
        }


def validate_sha256(value: str, field: str) -> str:
    if not SHA256_PATTERN.fullmatch(value):
        raise ValueError(f"{field} must be a SHA-256 digest")
    return value


def validate_git_commit_sha(value: str, field: str = "commit_sha") -> str:
    if not GIT_COMMIT_PATTERN.fullmatch(value):
        raise ValueError(f"{field} must be a full Git commit hash")
    return value


def validate_actor_id(value: str) -> str:
    normalized = value.strip()
    if not IDENTIFIER_PATTERN.fullmatch(normalized):
        raise ValueError("actor_id is invalid")
    return normalized


def validate_idempotency_key(value: str) -> str:
    normalized = value.strip()
    if not IDEMPOTENCY_PATTERN.fullmatch(normalized):
        raise ValueError("idempotency_key is invalid")
    return normalized


def validate_approval_ttl(seconds: int) -> int:
    if (
        not isinstance(seconds, int)
        or isinstance(seconds, bool)
        or seconds < 60
        or seconds > 1_800
    ):
        raise ValueError("approval expiry must be between 60 and 1800 seconds")
    return seconds


def validate_pull_request_text(title: str, body: str) -> tuple[str, str]:
    normalized_title = title.strip()
    if not normalized_title or len(normalized_title) > 256:
        raise ValueError("pull-request title must contain 1 to 256 characters")
    if len(body) > 20_000:
        raise ValueError("pull-request body exceeds 20000 characters")
    return normalized_title, body


def validate_publication_result(
    pull_request_number: int, pull_request_url: str, head_sha: str
) -> tuple[int, str, str]:
    if (
        not isinstance(pull_request_number, int)
        or isinstance(pull_request_number, bool)
        or pull_request_number <= 0
    ):
        raise ValueError("pull-request number must be a positive integer")
    parsed = urlparse(pull_request_url)
    if parsed.scheme != "https" or not parsed.netloc or len(pull_request_url) > 2048:
        raise ValueError("pull-request URL must be an HTTPS URL")
    if not GIT_COMMIT_PATTERN.fullmatch(head_sha):
        raise ValueError("head_sha must be a full Git commit hash")
    return pull_request_number, pull_request_url, head_sha
