from __future__ import annotations

import hashlib

from forge_agent_core import (
    InMemoryIdempotencyLedger,
    ModelStepRequest,
    ModelStepResult,
    RunStoreProtocol,
    StopReason,
    TokenUsage,
    ToolCall,
)
from forge_evaluation import CheckKind, CheckSpec
from forge_sandbox_controller import SandboxController

from .evaluation_execution import EvaluationTemplate
from .main import execute_claimed_run


OFFLINE_DEMO_OBJECTIVE = (
    "Normalize user-entered status strings by trimming surrounding whitespace "
    "and converting input to lowercase without changing the public function contract."
)
OFFLINE_DEMO_PATCH = (
    "diff --git a/status.py b/status.py\n"
    "--- a/status.py\n"
    "+++ b/status.py\n"
    "@@ -2,7 +2,7 @@ VALID_STATUSES = {\"active\", \"paused\", \"disabled\"}\n"
    " \n"
    " \n"
    " def normalize_status(value: str) -> str:\n"
    "-    return value\n"
    "+    return value.strip().lower()\n"
    " \n"
    " \n"
    " def is_valid_status(value: str) -> bool:\n"
)


def _step(
    index: int,
    *,
    call: ToolCall | None = None,
    output: dict[str, object] | None = None,
) -> ModelStepResult:
    return ModelStepResult(
        provider="offline",
        model="forge-deterministic-demo-v1",
        response_id=f"offline-demo-step-{index}",
        stop_reason=(
            StopReason.TOOL_REQUESTED if call is not None else StopReason.NEEDS_APPROVAL
        ),
        usage=TokenUsage(),
        cost_microusd=0,
        prompt_version="forge-agent-v1",
        tool_version="forge-tools-v1",
        tool_calls=() if call is None else (call,),
        structured_output=output,
    )


class OfflineDemoRuntime:
    """Deterministic local runtime that exercises the agent loop without an API."""

    def __init__(self) -> None:
        patch_hash = hashlib.sha256(OFFLINE_DEMO_PATCH.encode("utf-8")).hexdigest()
        self._steps = (
            _step(
                1,
                call=ToolCall(
                    "offline-read-status",
                    "read_file",
                    {"path": "status.py", "start_line": 1, "end_line": 10},
                ),
            ),
            _step(
                2,
                call=ToolCall(
                    "offline-reproduce-test",
                    "execute_command",
                    {
                        "command": ["python", "-m", "unittest", "-v"],
                        "timeout_seconds": 20,
                    },
                ),
            ),
            _step(
                3,
                call=ToolCall(
                    "offline-apply-patch",
                    "apply_patch",
                    {
                        "patch": OFFLINE_DEMO_PATCH,
                        "expected_sha256": patch_hash,
                    },
                ),
            ),
            _step(
                4,
                call=ToolCall(
                    "offline-verify-test",
                    "execute_command",
                    {
                        "command": ["python", "-m", "unittest", "-v"],
                        "timeout_seconds": 20,
                    },
                ),
            ),
            _step(
                5,
                call=ToolCall("offline-capture-diff", "get_diff", {}),
            ),
            _step(
                6,
                output={
                    "status": "needs_approval",
                    "summary": "Status input is normalized before validation.",
                    "verification": [
                        "The original public regression was reproduced.",
                        "The public suite passes after the one-line patch.",
                    ],
                    "risks": [],
                },
            ),
        )
        self._index = 0

    def run_step(self, request: ModelStepRequest) -> ModelStepResult:
        if request.objective != OFFLINE_DEMO_OBJECTIVE:
            raise ValueError("offline demo runtime received an unexpected objective")
        if self._index >= len(self._steps):
            raise RuntimeError("offline demo runtime exhausted its deterministic steps")
        result = self._steps[self._index]
        self._index += 1
        return result


def offline_demo_evaluation_template() -> EvaluationTemplate:
    fixture_digest = hashlib.sha256(b"forge-status-normalizer-v1").hexdigest()
    image_digest = hashlib.sha256(b"forge-offline-demo-evaluator-v1").hexdigest()
    return EvaluationTemplate(
        benchmark_version="offline-product-tour-v1",
        task_id="status-normalizer-public-smoke",
        task_version="1",
        fixture_version=fixture_digest,
        sandbox_image_digest=f"forge-offline-demo@sha256:{image_digest}",
        public_checks=(
            CheckSpec(
                "public-regression",
                CheckKind.PUBLIC_TEST,
                ("python", "-m", "unittest", "-v"),
                timeout_seconds=20,
            ),
            CheckSpec(
                "acceptance-smoke",
                CheckKind.PUBLIC_TEST,
                (
                    "python",
                    "-c",
                    "from status import normalize_status; "
                    "assert normalize_status(' Active ') == 'active'",
                ),
                timeout_seconds=20,
            ),
            CheckSpec(
                "invalid-value-guard",
                CheckKind.PUBLIC_TEST,
                (
                    "python",
                    "-c",
                    "from status import is_valid_status; "
                    "assert not is_valid_status('archived')",
                ),
                timeout_seconds=20,
            ),
            CheckSpec(
                "syntax-check",
                CheckKind.TYPECHECK,
                ("python", "-m", "py_compile", "status.py", "test_status.py"),
                timeout_seconds=20,
            ),
        ),
    )


def execute_offline_demo(
    store: RunStoreProtocol,
    run_id: str,
    controller: SandboxController,
) -> dict[str, object]:
    worker_id = f"offline-demo:{run_id}"
    claimed = store.claim_run(run_id, worker_id, lease_seconds=30)
    if claimed is None:
        raise RuntimeError("offline demo run could not be claimed")
    execute_claimed_run(
        store,
        claimed,
        worker_id,
        controller=controller,
        model_runtime=OfflineDemoRuntime(),
        idempotency_ledger=InMemoryIdempotencyLedger(),
        evaluation_template=offline_demo_evaluation_template(),
    )
    return store.get_run(run_id)
