from __future__ import annotations

from .models import CheckResult, CheckStatus, PatchStats, PolicyFinding, RubricResult


class DeterministicRubricGrader:
    def __init__(self, version: str = "deterministic-rubric-v1") -> None:
        self.version = version

    def grade(
        self,
        patch_stats: PatchStats,
        findings: tuple[PolicyFinding, ...],
        checks: tuple[CheckResult, ...],
    ) -> RubricResult:
        required = tuple(item for item in checks if item.required)
        scores = {
            "policy_compliance": 1 if not findings else 0,
            "required_checks": (
                1
                if required
                and all(item.status == CheckStatus.PASSED for item in required)
                else 0
            ),
            "minimal_scope": (
                1
                if 0 < len(patch_stats.changed_paths) <= 10
                and patch_stats.added_lines + patch_stats.deleted_lines <= 500
                else 0
            ),
        }
        notes: list[str] = []
        if not scores["policy_compliance"]:
            notes.append("patch policy did not pass")
        if not scores["required_checks"]:
            notes.append("one or more required checks did not pass")
        if not scores["minimal_scope"]:
            notes.append("patch scope exceeds the deterministic rubric")
        return RubricResult(
            self.version,
            all(value == 1 for value in scores.values()),
            scores,
            tuple(notes),
        )
