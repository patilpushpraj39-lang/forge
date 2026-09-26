from __future__ import annotations

import json
from collections.abc import Callable

from forge_sandbox_controller import (
    ArtifactRef,
    CommandStatus,
    InvalidPatchError,
    SandboxController,
)

from .models import (
    CheckResult,
    CheckSpec,
    CheckStatus,
    EvaluationEvidence,
    EvaluationManifest,
    EvaluationReport,
    FailureCode,
    PatchPolicy,
    PatchStats,
    PolicyFinding,
    Verdict,
)
from .patch_policy import PatchParseError, inspect_hidden_patch, inspect_patch
from .rubric import DeterministicRubricGrader


REPORT_MEDIA_TYPE = "application/vnd.forge.evaluation+json"


class EvaluationRunner:
    def __init__(
        self,
        controller: SandboxController,
        *,
        policy: PatchPolicy | None = None,
        grader: DeterministicRubricGrader | None = None,
    ) -> None:
        self.controller = controller
        self.policy = policy or PatchPolicy()
        self.grader = grader or DeterministicRubricGrader()

    def evaluate(
        self,
        manifest: EvaluationManifest,
        snapshot: ArtifactRef,
        patch: ArtifactRef,
        *,
        hidden_patch: ArtifactRef | None = None,
        should_cancel: Callable[[], bool] = lambda: False,
        heartbeat: Callable[[], None] = lambda: None,
    ) -> EvaluationEvidence:
        mismatch = self._manifest_mismatches(
            manifest, snapshot, patch, hidden_patch
        )
        if mismatch:
            return self._store_report(
                self._early_report(
                    manifest,
                    patch,
                    PatchStats(patch.size_bytes, (), 0, 0),
                    mismatch,
                    FailureCode.MANIFEST_MISMATCH,
                )
            )
        try:
            patch_bytes = self.controller.read_artifact(
                patch, max_bytes=max(patch.size_bytes, 1)
            )
            patch_stats, findings = inspect_patch(patch_bytes, self.policy)
        except (PatchParseError, ValueError) as error:
            finding = PolicyFinding(FailureCode.PATCH_INVALID, str(error))
            return self._store_report(
                self._early_report(
                    manifest,
                    patch,
                    PatchStats(patch.size_bytes, (), 0, 0),
                    (finding,),
                    FailureCode.PATCH_INVALID,
                )
            )
        if findings:
            return self._store_report(
                self._early_report(
                    manifest,
                    patch,
                    patch_stats,
                    findings,
                    *tuple(item.code for item in findings),
                )
            )

        hidden_bytes: bytes | None = None
        if manifest.hidden_checks:
            if hidden_patch is None:
                finding = PolicyFinding(
                    FailureCode.HIDDEN_TEST_MISSING,
                    "hidden checks require a hidden-test artifact",
                )
                return self._store_report(
                    self._early_report(
                        manifest,
                        patch,
                        patch_stats,
                        (finding,),
                        FailureCode.HIDDEN_TEST_MISSING,
                    )
                )
            try:
                hidden_bytes = self.controller.read_artifact(
                    hidden_patch, max_bytes=max(hidden_patch.size_bytes, 1)
                )
                inspect_hidden_patch(hidden_bytes)
            except (PatchParseError, ValueError) as error:
                finding = PolicyFinding(
                    FailureCode.HIDDEN_TEST_INVALID, str(error)
                )
                return self._store_report(
                    self._early_report(
                        manifest,
                        patch,
                        patch_stats,
                        (finding,),
                        FailureCode.HIDDEN_TEST_INVALID,
                    )
                )

        checks: list[CheckResult] = []
        sandbox = self.controller.create_from_snapshot(snapshot)
        try:
            if sandbox.snapshot_hash != manifest.snapshot_hash:
                finding = PolicyFinding(
                    FailureCode.MANIFEST_MISMATCH,
                    "reconstructed repository hash does not match the manifest",
                )
                return self._store_report(
                    self._early_report(
                        manifest,
                        patch,
                        patch_stats,
                        (finding,),
                        FailureCode.MANIFEST_MISMATCH,
                    )
                )
            self.controller.apply_patch(
                sandbox.sandbox_id, patch_bytes, manifest.patch_hash
            )
            checks.extend(
                self._run_checks(
                    sandbox.sandbox_id,
                    manifest.public_checks,
                    should_cancel,
                    heartbeat,
                )
            )
            if hidden_bytes is not None and not should_cancel():
                self.controller.apply_patch(
                    sandbox.sandbox_id,
                    hidden_bytes,
                    manifest.hidden_patch_hash,
                )
                checks.extend(
                    self._run_checks(
                        sandbox.sandbox_id,
                        manifest.hidden_checks,
                        should_cancel,
                        heartbeat,
                    )
                )
        except InvalidPatchError as error:
            finding = PolicyFinding(FailureCode.PATCH_INVALID, str(error)[:512])
            return self._store_report(
                self._early_report(
                    manifest,
                    patch,
                    patch_stats,
                    (finding,),
                    FailureCode.PATCH_INVALID,
                )
            )
        finally:
            self.controller.destroy(sandbox.sandbox_id)

        check_failures = self._check_failure_codes(tuple(checks))
        rubric = self.grader.grade(patch_stats, (), tuple(checks))
        failures = list(check_failures)
        if not rubric.passed:
            failures.append(FailureCode.RUBRIC_FAILED)
        unique_failures = tuple(dict.fromkeys(failures))
        report = EvaluationReport(
            schema_version=1,
            verdict=Verdict.PASSED if not unique_failures else Verdict.FAILED,
            manifest_digest=manifest.digest,
            snapshot_hash=manifest.snapshot_hash,
            patch_hash=patch.sha256,
            patch_stats=patch_stats,
            policy_findings=(),
            checks=tuple(checks),
            rubric=rubric,
            failure_codes=unique_failures,
        )
        return self._store_report(report)

    def _run_checks(
        self,
        sandbox_id: str,
        specs: tuple[CheckSpec, ...],
        should_cancel: Callable[[], bool],
        heartbeat: Callable[[], None],
    ) -> tuple[CheckResult, ...]:
        results: list[CheckResult] = []
        for spec in specs:
            if should_cancel():
                results.append(
                    CheckResult(
                        spec.check_id,
                        spec.kind,
                        CheckStatus.CANCELLED,
                        spec.required,
                        None,
                        "",
                        False,
                    )
                )
                continue
            command = self.controller.execute(
                sandbox_id,
                spec.command,
                spec.timeout_seconds,
                should_cancel,
                heartbeat,
                max(0.05, min(1.0, spec.timeout_seconds / 3)),
                spec.output_limit_bytes,
            )
            results.append(
                CheckResult(
                    spec.check_id,
                    spec.kind,
                    _check_status(command.status),
                    spec.required,
                    command.exit_code,
                    command.output,
                    command.output_truncated,
                )
            )
        return tuple(results)

    def _manifest_mismatches(
        self,
        manifest: EvaluationManifest,
        snapshot: ArtifactRef,
        patch: ArtifactRef,
        hidden_patch: ArtifactRef | None,
    ) -> tuple[PolicyFinding, ...]:
        messages: list[str] = []
        if manifest.snapshot_artifact_hash != snapshot.sha256:
            messages.append("snapshot artifact hash does not match the manifest")
        if manifest.patch_hash != patch.sha256:
            messages.append("patch hash does not match the manifest")
        if hidden_patch is not None and manifest.hidden_patch_hash != hidden_patch.sha256:
            messages.append("hidden patch hash does not match the manifest")
        if manifest.policy_version != self.policy.version:
            messages.append("patch policy version does not match the manifest")
        if manifest.grader_version != self.grader.version:
            messages.append("rubric grader version does not match the manifest")
        return tuple(
            PolicyFinding(FailureCode.MANIFEST_MISMATCH, message)
            for message in messages
        )

    def _early_report(
        self,
        manifest: EvaluationManifest,
        patch: ArtifactRef,
        stats: PatchStats,
        findings: tuple[PolicyFinding, ...],
        *failure_codes: FailureCode,
    ) -> EvaluationReport:
        rubric = self.grader.grade(stats, findings, ())
        failures = tuple(
            dict.fromkeys((*failure_codes, FailureCode.RUBRIC_FAILED))
        )
        return EvaluationReport(
            1,
            Verdict.FAILED,
            manifest.digest,
            manifest.snapshot_hash,
            patch.sha256,
            stats,
            findings,
            (),
            rubric,
            failures,
        )

    def _store_report(self, report: EvaluationReport) -> EvaluationEvidence:
        content = (
            json.dumps(
                report.to_dict(),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
            + "\n"
        ).encode("utf-8")
        artifact = self.controller.store_artifact(content, REPORT_MEDIA_TYPE)
        return EvaluationEvidence(report, artifact)

    @staticmethod
    def _check_failure_codes(
        checks: tuple[CheckResult, ...]
    ) -> tuple[FailureCode, ...]:
        mapping = {
            CheckStatus.FAILED: FailureCode.CHECK_FAILED,
            CheckStatus.TIMED_OUT: FailureCode.CHECK_TIMED_OUT,
            CheckStatus.OUTPUT_LIMIT: FailureCode.CHECK_OUTPUT_LIMIT,
            CheckStatus.RESOURCE_LIMIT: FailureCode.CHECK_RESOURCE_LIMIT,
            CheckStatus.CANCELLED: FailureCode.CHECK_CANCELLED,
        }
        return tuple(
            dict.fromkeys(
                mapping[item.status]
                for item in checks
                if item.required and item.status in mapping
            )
        )


def _check_status(status: CommandStatus) -> CheckStatus:
    return {
        CommandStatus.COMPLETED: CheckStatus.PASSED,
        CommandStatus.FAILED: CheckStatus.FAILED,
        CommandStatus.CANCELLED: CheckStatus.CANCELLED,
        CommandStatus.TIMED_OUT: CheckStatus.TIMED_OUT,
        CommandStatus.OUTPUT_LIMIT: CheckStatus.OUTPUT_LIMIT,
        CommandStatus.RESOURCE_LIMIT: CheckStatus.RESOURCE_LIMIT,
    }[status]
