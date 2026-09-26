from __future__ import annotations

import fnmatch
from pathlib import PurePosixPath

from .models import FailureCode, PatchPolicy, PatchStats, PolicyFinding


class PatchParseError(ValueError):
    pass


def inspect_patch(
    patch: bytes, policy: PatchPolicy
) -> tuple[PatchStats, tuple[PolicyFinding, ...]]:
    if not patch:
        stats = PatchStats(0, (), 0, 0)
        return stats, (
            PolicyFinding(FailureCode.PATCH_EMPTY, "patch is empty"),
        )
    if len(patch) > policy.max_patch_bytes:
        empty = PatchStats(len(patch), (), 0, 0)
        return empty, (
            PolicyFinding(
                FailureCode.PATCH_TOO_LARGE,
                f"patch exceeds {policy.max_patch_bytes} bytes",
            ),
        )
    if b"\0" in patch:
        raise PatchParseError("binary patch content is not supported")
    try:
        text = patch.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PatchParseError("patch must be UTF-8") from error
    if "GIT binary patch" in text or "120000" in text:
        raise PatchParseError("binary and symlink patches are not supported")

    changed_paths: set[str] = set()
    added_lines = 0
    deleted_lines = 0
    in_hunk = False
    for line in text.splitlines():
        if line.startswith("diff --git "):
            in_hunk = False
        elif line.startswith("@@ "):
            in_hunk = True
        elif line.startswith("+++ "):
            candidate = line[4:].split("\t", 1)[0]
            if candidate != "/dev/null":
                changed_paths.add(_safe_patch_path(candidate, "b/"))
        elif line.startswith("--- "):
            candidate = line[4:].split("\t", 1)[0]
            if candidate != "/dev/null":
                changed_paths.add(_safe_patch_path(candidate, "a/"))
        elif in_hunk and line.startswith("+"):
            added_lines += 1
        elif in_hunk and line.startswith("-"):
            deleted_lines += 1
    if not changed_paths:
        raise PatchParseError("patch contains no valid file headers")

    paths = tuple(sorted(changed_paths))
    stats = PatchStats(len(patch), paths, added_lines, deleted_lines)
    findings: list[PolicyFinding] = []
    if len(paths) > policy.max_changed_files:
        findings.append(
            PolicyFinding(
                FailureCode.TOO_MANY_FILES,
                f"patch changes {len(paths)} files",
            )
        )
    if added_lines > policy.max_added_lines:
        findings.append(
            PolicyFinding(
                FailureCode.TOO_MANY_ADDITIONS,
                f"patch adds {added_lines} lines",
            )
        )
    if deleted_lines > policy.max_deleted_lines:
        findings.append(
            PolicyFinding(
                FailureCode.TOO_MANY_DELETIONS,
                f"patch deletes {deleted_lines} lines",
            )
        )
    for path in paths:
        if _matches_any(path, policy.forbidden_patterns):
            findings.append(
                PolicyFinding(
                    FailureCode.FORBIDDEN_PATH,
                    "patch changes a forbidden path",
                    path,
                )
            )
        if not policy.allow_test_changes and _matches_any(
            path, policy.protected_test_patterns
        ):
            findings.append(
                PolicyFinding(
                    FailureCode.PROTECTED_TEST_CHANGED,
                    "solver patches cannot modify protected tests",
                    path,
                )
            )
    return stats, tuple(findings)


def inspect_hidden_patch(patch: bytes) -> PatchStats:
    permissive = PatchPolicy(
        max_patch_bytes=256_000,
        max_changed_files=20,
        max_added_lines=5_000,
        max_deleted_lines=1,
        forbidden_patterns=(),
        protected_test_patterns=(),
        allow_test_changes=True,
    )
    stats, findings = inspect_patch(patch, permissive)
    if findings:
        raise PatchParseError("hidden patch exceeds evaluator limits")
    if stats.deleted_lines:
        raise PatchParseError("hidden patch cannot delete existing content")
    if any(
        "_forge_hidden" not in PurePosixPath(path).name
        for path in stats.changed_paths
    ):
        raise PatchParseError("hidden patch paths must contain _forge_hidden")
    return stats


def _safe_patch_path(value: str, prefix: str) -> str:
    if not value.startswith(prefix):
        raise PatchParseError("patch paths must use a/ and b/ prefixes")
    relative = PurePosixPath(value[len(prefix) :])
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise PatchParseError("patch path escapes the repository")
    return relative.as_posix()


def _matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)
