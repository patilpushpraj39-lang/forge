from __future__ import annotations

import hashlib
import json
import os
import shlex
from dataclasses import dataclass

from forge_agent_core import RunState, RunStoreProtocol
from forge_evaluation import (
    CheckKind,
    CheckSpec,
    EvaluationEvidence,
    EvaluationManifest,
    EvaluationRunner,
    Verdict,
)
from forge_repo_intelligence import RepositoryManifest
from forge_sandbox_controller import (
    ArtifactRef,
    SandboxController,
    SandboxHandle,
)

from .agent_execution import (
    PROMPT_VERSION,
    TOOL_VERSION,
    AgentExecutionEvidence,
)


DEFAULT_LOCAL_EVALUATION_IMAGE = "forge-local-development@sha256:" + hashlib.sha256(
    b"forge-local-development-evaluator-v1"
).hexdigest()
MANIFEST_MEDIA_TYPE = "application/vnd.forge.evaluation-manifest+json"


@dataclass(frozen=True)
class EvaluationTemplate:
    benchmark_version: str
    task_id: str
    task_version: str
    fixture_version: str
    sandbox_image_digest: str
    public_checks: tuple[CheckSpec, ...]
    hidden_checks: tuple[CheckSpec, ...] = ()
    hidden_patch: ArtifactRef | None = None


def default_evaluation_template(
    run: dict[str, object], repository: RepositoryManifest
) -> EvaluationTemplate:
    checks: list[CheckSpec] = []
    for index, command_text in enumerate(repository.test_commands, start=1):
        command = tuple(shlex.split(command_text, posix=True))
        checks.append(
            CheckSpec(
                f"repository-test-{index}",
                _kind_for_command(command),
                command,
                timeout_seconds=120,
            )
        )
    objective = str(run["objective"])
    return EvaluationTemplate(
        benchmark_version="adhoc-run-v1",
        task_id=str(run["run_id"]),
        task_version=hashlib.sha256(objective.encode("utf-8")).hexdigest(),
        fixture_version=repository.manifest_hash,
        sandbox_image_digest=os.environ.get(
            "FORGE_EVALUATION_IMAGE_DIGEST",
            DEFAULT_LOCAL_EVALUATION_IMAGE,
        ),
        public_checks=tuple(checks),
    )


def execute_independent_evaluation(
    store: RunStoreProtocol,
    run: dict[str, object],
    worker_id: str,
    controller: SandboxController,
    snapshot: SandboxHandle,
    repository: RepositoryManifest,
    agent: AgentExecutionEvidence,
    *,
    lease_seconds: float,
    template: EvaluationTemplate | None = None,
) -> EvaluationEvidence:
    if snapshot.snapshot_artifact is None:
        raise ValueError("evaluation requires an immutable snapshot artifact")
    if agent.patch_artifact is None or not agent.changed_paths:
        raise ValueError("evaluation requires a non-empty patch artifact")
    selected = template or default_evaluation_template(run, repository)
    hidden_hash = (
        selected.hidden_patch.sha256
        if selected.hidden_patch is not None
        else None
    )
    if selected.hidden_checks and selected.hidden_patch is None:
        raise ValueError("hidden checks require a hidden patch artifact")
    model_version = (
        agent.result.model_steps[-1].model
        if agent.result.model_steps
        else "unknown-model"
    )
    manifest = EvaluationManifest(
        schema_version=1,
        benchmark_version=selected.benchmark_version,
        task_id=selected.task_id,
        task_version=selected.task_version,
        fixture_version=selected.fixture_version,
        snapshot_hash=snapshot.snapshot_hash,
        snapshot_artifact_hash=snapshot.snapshot_artifact.sha256,
        patch_hash=agent.patch_artifact.sha256,
        hidden_patch_hash=hidden_hash,
        sandbox_image_digest=selected.sandbox_image_digest,
        prompt_version=PROMPT_VERSION,
        tool_version=TOOL_VERSION,
        model_version=model_version,
        grader_version="deterministic-rubric-v1",
        policy_version="patch-policy-v1",
        public_checks=selected.public_checks,
        hidden_checks=selected.hidden_checks,
    )
    manifest_artifact = controller.store_artifact(
        (
            json.dumps(
                manifest.to_dict(),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
            + "\n"
        ).encode("utf-8"),
        MANIFEST_MEDIA_TYPE,
    )
    run_id = str(run["run_id"])
    store.transition(
        run_id,
        RunState.EXECUTING,
        RunState.EVALUATING,
        "worker",
        {
            "manifest_digest": manifest.digest,
            "manifest_artifact": manifest_artifact.to_dict(),
        },
        lease_owner=worker_id,
    )
    store.append_event(
        run_id,
        "evaluation_started",
        "evaluator",
        {
            "manifest_digest": manifest.digest,
            "manifest_artifact": manifest_artifact.to_dict(),
            "snapshot_artifact": snapshot.snapshot_artifact.to_dict(),
            "patch_artifact": agent.patch_artifact.to_dict(),
            "hidden_patch_artifact": (
                selected.hidden_patch.to_dict()
                if selected.hidden_patch is not None
                else None
            ),
            "benchmark_version": manifest.benchmark_version,
            "policy_version": manifest.policy_version,
            "grader_version": manifest.grader_version,
            "sandbox_image_digest": manifest.sandbox_image_digest,
            "public_check_ids": [item.check_id for item in manifest.public_checks],
            "hidden_check_ids": [item.check_id for item in manifest.hidden_checks],
        },
    )
    runner = EvaluationRunner(controller)
    result = runner.evaluate(
        manifest,
        snapshot.snapshot_artifact,
        agent.patch_artifact,
        hidden_patch=selected.hidden_patch,
        should_cancel=lambda: store.is_cancellation_requested(run_id),
        heartbeat=lambda: store.renew_lease(
            run_id, worker_id, lease_seconds
        ),
    )
    store.append_event(
        run_id,
        "evaluation_completed",
        "evaluator",
        {
            "verdict": result.report.verdict,
            "verdict_hash": result.report.verdict_hash,
            "manifest_digest": result.report.manifest_digest,
            "manifest_artifact": manifest_artifact.to_dict(),
            "report_artifact": result.report_artifact.to_dict(),
            "failure_codes": list(result.report.failure_codes),
            "checks": [
                {
                    "check_id": item.check_id,
                    "kind": item.kind,
                    "status": item.status,
                    "required": item.required,
                }
                for item in result.report.checks
            ],
            "rubric": result.report.rubric.to_dict(),
        },
    )
    if store.is_cancellation_requested(run_id):
        store.acknowledge_cancellation(run_id, worker_id)
    elif result.report.verdict == Verdict.PASSED:
        store.transition(
            run_id,
            RunState.EVALUATING,
            RunState.AWAITING_APPROVAL,
            "evaluator",
            {
                "diff_hash": agent.diff_hash,
                "changed_paths": list(agent.changed_paths),
                "verdict_hash": result.report.verdict_hash,
                "report_artifact": result.report_artifact.to_dict(),
            },
            lease_owner=worker_id,
        )
    else:
        store.transition(
            run_id,
            RunState.EVALUATING,
            RunState.FAILED,
            "evaluator",
            {
                "reason": "independent_evaluation_failed",
                "verdict_hash": result.report.verdict_hash,
                "failure_codes": list(result.report.failure_codes),
            },
            lease_owner=worker_id,
        )
    return result


def _kind_for_command(command: tuple[str, ...]) -> CheckKind:
    rendered = " ".join(command).casefold()
    if "lint" in rendered:
        return CheckKind.LINT
    if "typecheck" in rendered or "tsc" in rendered or "mypy" in rendered:
        return CheckKind.TYPECHECK
    if "build" in rendered:
        return CheckKind.BUILD
    return CheckKind.PUBLIC_TEST
