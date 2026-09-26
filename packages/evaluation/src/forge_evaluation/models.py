from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from forge_sandbox_controller import ArtifactRef


SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class Verdict(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"


class CheckKind(StrEnum):
    SETUP = "setup"
    BUILD = "build"
    LINT = "lint"
    TYPECHECK = "typecheck"
    PUBLIC_TEST = "public_test"
    HIDDEN_TEST = "hidden_test"


class CheckStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    OUTPUT_LIMIT = "output_limit"
    RESOURCE_LIMIT = "resource_limit"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class FailureCode(StrEnum):
    PATCH_EMPTY = "patch_empty"
    PATCH_INVALID = "patch_invalid"
    PATCH_TOO_LARGE = "patch_too_large"
    TOO_MANY_FILES = "too_many_files"
    TOO_MANY_ADDITIONS = "too_many_additions"
    TOO_MANY_DELETIONS = "too_many_deletions"
    FORBIDDEN_PATH = "forbidden_path"
    PROTECTED_TEST_CHANGED = "protected_test_changed"
    MANIFEST_MISMATCH = "manifest_mismatch"
    HIDDEN_TEST_MISSING = "hidden_test_missing"
    HIDDEN_TEST_INVALID = "hidden_test_invalid"
    CHECK_FAILED = "check_failed"
    CHECK_TIMED_OUT = "check_timed_out"
    CHECK_OUTPUT_LIMIT = "check_output_limit"
    CHECK_RESOURCE_LIMIT = "check_resource_limit"
    CHECK_CANCELLED = "check_cancelled"
    RUBRIC_FAILED = "rubric_failed"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True)
class PatchPolicy:
    version: str = "patch-policy-v1"
    max_patch_bytes: int = 128_000
    max_changed_files: int = 20
    max_added_lines: int = 500
    max_deleted_lines: int = 500
    forbidden_patterns: tuple[str, ...] = (
        ".git/*",
        ".github/workflows/*",
        ".env*",
        "infra/*",
        "**/*.pem",
        "**/*.key",
    )
    protected_test_patterns: tuple[str, ...] = (
        "tests/*",
        "test/*",
        "**/tests/*",
        "**/test/*",
        "test_*.py",
        "**/test_*.py",
        "*.test.*",
        "**/*.test.*",
        "*.spec.*",
        "**/*.spec.*",
    )
    allow_test_changes: bool = False

    def __post_init__(self) -> None:
        numeric = (
            self.max_patch_bytes,
            self.max_changed_files,
            self.max_added_lines,
            self.max_deleted_lines,
        )
        if any(
            not isinstance(value, int)
            or isinstance(value, bool)
            or value <= 0
            for value in numeric
        ):
            raise ValueError("patch-policy limits must be positive integers")

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "max_patch_bytes": self.max_patch_bytes,
            "max_changed_files": self.max_changed_files,
            "max_added_lines": self.max_added_lines,
            "max_deleted_lines": self.max_deleted_lines,
            "forbidden_patterns": list(self.forbidden_patterns),
            "protected_test_patterns": list(self.protected_test_patterns),
            "allow_test_changes": self.allow_test_changes,
        }


@dataclass(frozen=True)
class CheckSpec:
    check_id: str
    kind: CheckKind
    command: tuple[str, ...]
    timeout_seconds: float = 60
    output_limit_bytes: int = 64 * 1024
    required: bool = True

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", self.check_id):
            raise ValueError(f"invalid check id: {self.check_id}")
        if not self.command or len(self.command) > 32:
            raise ValueError("check command must contain 1 to 32 arguments")
        if any(not item or len(item) > 1024 for item in self.command):
            raise ValueError("check command contains an invalid argument")
        executable = self.command[0].casefold()
        if executable not in {"node", "npm", "pnpm", "python", "python3"}:
            raise ValueError(f"check executable is not allowed: {executable}")
        if (
            not isinstance(self.timeout_seconds, (int, float))
            or isinstance(self.timeout_seconds, bool)
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
            or self.timeout_seconds > 300
        ):
            raise ValueError("check timeout must be between 0 and 300 seconds")
        if self.output_limit_bytes <= 0 or self.output_limit_bytes > 1_000_000:
            raise ValueError("check output limit is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "kind": self.kind,
            "command": list(self.command),
            "timeout_seconds": self.timeout_seconds,
            "output_limit_bytes": self.output_limit_bytes,
            "required": self.required,
        }


@dataclass(frozen=True)
class EvaluationManifest:
    schema_version: int
    benchmark_version: str
    task_id: str
    task_version: str
    fixture_version: str
    snapshot_hash: str
    snapshot_artifact_hash: str
    patch_hash: str
    sandbox_image_digest: str
    prompt_version: str
    tool_version: str
    model_version: str
    grader_version: str
    policy_version: str
    public_checks: tuple[CheckSpec, ...]
    hidden_checks: tuple[CheckSpec, ...] = ()
    hidden_patch_hash: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported evaluation manifest schema")
        for name in (
            "snapshot_hash",
            "snapshot_artifact_hash",
            "patch_hash",
        ):
            if not SHA256_PATTERN.fullmatch(getattr(self, name)):
                raise ValueError(f"{name} must be a SHA-256 digest")
        if self.hidden_patch_hash is not None and not SHA256_PATTERN.fullmatch(
            self.hidden_patch_hash
        ):
            raise ValueError("hidden_patch_hash must be a SHA-256 digest")
        if self.hidden_checks and self.hidden_patch_hash is None:
            raise ValueError("hidden checks require a hidden patch hash")
        if not re.fullmatch(r"[^@\s]+@sha256:[0-9a-f]{64}", self.sandbox_image_digest):
            raise ValueError("sandbox image must use an immutable SHA-256 digest")
        versions = (
            self.benchmark_version,
            self.task_id,
            self.task_version,
            self.fixture_version,
            self.prompt_version,
            self.tool_version,
            self.model_version,
            self.grader_version,
            self.policy_version,
        )
        if any(not value or len(value) > 128 for value in versions):
            raise ValueError("manifest version fields must be 1 to 128 characters")
        check_ids = [
            item.check_id for item in (*self.public_checks, *self.hidden_checks)
        ]
        if len(check_ids) != len(set(check_ids)):
            raise ValueError("evaluation check ids must be unique")
        if any(item.kind == CheckKind.HIDDEN_TEST for item in self.public_checks):
            raise ValueError("hidden tests cannot be public checks")
        if any(item.kind != CheckKind.HIDDEN_TEST for item in self.hidden_checks):
            raise ValueError("hidden checks must use the hidden_test kind")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "benchmark_version": self.benchmark_version,
            "task_id": self.task_id,
            "task_version": self.task_version,
            "fixture_version": self.fixture_version,
            "snapshot_hash": self.snapshot_hash,
            "snapshot_artifact_hash": self.snapshot_artifact_hash,
            "patch_hash": self.patch_hash,
            "hidden_patch_hash": self.hidden_patch_hash,
            "sandbox_image_digest": self.sandbox_image_digest,
            "prompt_version": self.prompt_version,
            "tool_version": self.tool_version,
            "model_version": self.model_version,
            "grader_version": self.grader_version,
            "policy_version": self.policy_version,
            "public_checks": [item.to_dict() for item in self.public_checks],
            "hidden_checks": [item.to_dict() for item in self.hidden_checks],
        }

    @property
    def digest(self) -> str:
        return _canonical_sha256(self.to_dict())


@dataclass(frozen=True)
class PatchStats:
    size_bytes: int
    changed_paths: tuple[str, ...]
    added_lines: int
    deleted_lines: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "size_bytes": self.size_bytes,
            "changed_paths": list(self.changed_paths),
            "added_lines": self.added_lines,
            "deleted_lines": self.deleted_lines,
        }


@dataclass(frozen=True)
class PolicyFinding:
    code: FailureCode
    message: str
    path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "path": self.path}


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    kind: CheckKind
    status: CheckStatus
    required: bool
    exit_code: int | None
    output: str
    output_truncated: bool

    def to_dict(self, *, include_output: bool = True) -> dict[str, Any]:
        result = {
            "check_id": self.check_id,
            "kind": self.kind,
            "status": self.status,
            "required": self.required,
            "exit_code": self.exit_code,
            "output_truncated": self.output_truncated,
        }
        if include_output:
            result["output"] = self.output
        return result


@dataclass(frozen=True)
class RubricResult:
    grader_version: str
    passed: bool
    scores: dict[str, int]
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "grader_version": self.grader_version,
            "passed": self.passed,
            "scores": dict(sorted(self.scores.items())),
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class EvaluationReport:
    schema_version: int
    verdict: Verdict
    manifest_digest: str
    snapshot_hash: str
    patch_hash: str
    patch_stats: PatchStats
    policy_findings: tuple[PolicyFinding, ...]
    checks: tuple[CheckResult, ...]
    rubric: RubricResult
    failure_codes: tuple[FailureCode, ...]

    def to_dict(self, *, include_output: bool = True) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "verdict": self.verdict,
            "manifest_digest": self.manifest_digest,
            "snapshot_hash": self.snapshot_hash,
            "patch_hash": self.patch_hash,
            "patch_stats": self.patch_stats.to_dict(),
            "policy_findings": [item.to_dict() for item in self.policy_findings],
            "checks": [
                item.to_dict(include_output=include_output) for item in self.checks
            ],
            "rubric": self.rubric.to_dict(),
            "failure_codes": list(self.failure_codes),
        }

    @property
    def verdict_hash(self) -> str:
        return _canonical_sha256(self.to_dict(include_output=False))


@dataclass(frozen=True)
class EvaluationEvidence:
    report: EvaluationReport
    report_artifact: ArtifactRef


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
