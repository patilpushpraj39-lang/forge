from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable

from forge_agent_core import (
    AgentLoopRequest,
    AgentLoopResult,
    BoundedAgentLoop,
    BudgetLimits,
    ContextReference,
    IdempotencyLedger,
    ModelRuntime,
    ModelStepRequest,
    ModelStepResult,
    RunState,
    RunStoreProtocol,
    StopReason,
)
from forge_sandbox_controller import ArtifactRef, SandboxController

from .tools import ForgeToolExecutor, forge_tool_specs


PROMPT_VERSION = "forge-agent-v1"
TOOL_VERSION = "forge-tools-v1"
INITIAL_CONTEXT_CHARACTERS = 16_000

AGENT_OUTPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": ["completed", "needs_approval", "blocked"],
        },
        "summary": {"type": "string", "maxLength": 4_000},
        "verification": {
            "type": "array",
            "maxItems": 20,
            "items": {"type": "string", "maxLength": 1_000},
        },
        "risks": {
            "type": "array",
            "maxItems": 20,
            "items": {"type": "string", "maxLength": 1_000},
        },
    },
    "required": ["status", "summary", "verification", "risks"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class AgentExecutionEvidence:
    result: AgentLoopResult
    changed_paths: tuple[str, ...]
    diff_hash: str | None
    patch_artifact: ArtifactRef | None


class LeaseHeartbeatingRuntime:
    """Keeps the durable worker lease alive during a blocking provider call."""

    def __init__(
        self,
        runtime: ModelRuntime,
        heartbeat: Callable[[], None],
        interval_seconds: float,
    ) -> None:
        self.runtime = runtime
        self.heartbeat = heartbeat
        self.interval_seconds = max(0.05, interval_seconds)

    def run_step(self, request: ModelStepRequest) -> ModelStepResult:
        stopped = threading.Event()
        errors: list[Exception] = []

        def renew() -> None:
            while not stopped.wait(self.interval_seconds):
                try:
                    self.heartbeat()
                except Exception as error:
                    errors.append(error)
                    stopped.set()

        thread = threading.Thread(target=renew, daemon=True)
        thread.start()
        try:
            result = self.runtime.run_step(request)
        finally:
            stopped.set()
            thread.join(timeout=max(1.0, self.interval_seconds * 2))
        if errors:
            raise errors[0]
        return result


def execute_agent_loop(
    store: RunStoreProtocol,
    run: dict[str, object],
    worker_id: str,
    controller: SandboxController,
    sandbox_id: str,
    runtime: ModelRuntime,
    ledger: IdempotencyLedger,
    *,
    lease_seconds: float,
) -> AgentExecutionEvidence:
    run_id = str(run["run_id"])
    objective = str(run["objective"])
    ranked = controller.rank_relevant_files(
        sandbox_id, objective, limit=12
    )
    context_pack = controller.build_context_pack(
        sandbox_id,
        ranked,
        budget_characters=INITIAL_CONTEXT_CHARACTERS,
    )
    included = {item.provenance for item in context_pack.items}
    context_refs = tuple(
        ContextReference(
            result.snapshot_hash,
            result.path,
            result.start_line,
            result.end_line,
            result.snippet,
        )
        for result in ranked
        if result.provenance in included
    )
    budgets = BudgetLimits(
        total_tokens=int(run["budget_total_tokens"]),
        cost_microusd=int(run["budget_cost_microusd"]),
        wall_seconds=float(run["budget_wall_seconds"]),
        model_steps=int(run["budget_model_steps"]),
        tool_calls=int(run["budget_tool_calls"]),
        patch_attempts=int(run["budget_patch_attempts"]),
    )
    store.append_event(
        run_id,
        "agent_started",
        "worker",
        {
            "objective": objective,
            "prompt_version": PROMPT_VERSION,
            "tool_version": TOOL_VERSION,
            "budgets": {
                "total_tokens": budgets.total_tokens,
                "cost_microusd": budgets.cost_microusd,
                "wall_seconds": budgets.wall_seconds,
                "model_steps": budgets.model_steps,
                "tool_calls": budgets.tool_calls,
                "patch_attempts": budgets.patch_attempts,
            },
            "context_provenance": [item.provenance for item in context_refs],
            "context_characters": context_pack.used_characters,
            "context_truncated": context_pack.truncated,
        },
    )
    heartbeat_runtime = LeaseHeartbeatingRuntime(
        runtime,
        lambda: store.renew_lease(run_id, worker_id, lease_seconds),
        lease_seconds / 3,
    )
    executor = ForgeToolExecutor(
        controller,
        sandbox_id,
        should_cancel=lambda: store.is_cancellation_requested(run_id),
        heartbeat=lambda: store.renew_lease(
            run_id, worker_id, lease_seconds
        ),
    )
    result = BoundedAgentLoop(
        heartbeat_runtime, executor, ledger
    ).run(
        AgentLoopRequest(
            run_id=run_id,
            objective=objective,
            context_refs=context_refs,
            tools=forge_tool_specs(TOOL_VERSION),
            output_schema=AGENT_OUTPUT_SCHEMA,
            budgets=budgets,
            prompt_version=PROMPT_VERSION,
            tool_version=TOOL_VERSION,
        ),
        should_cancel=lambda: store.is_cancellation_requested(run_id),
    )
    structured_output = (
        result.model_steps[-1].structured_output
        if result.model_steps
        else None
    )
    store.append_event(
        run_id,
        "agent_stopped",
        "worker",
        {
            "stop_reason": result.stop_reason,
            "model_steps": len(result.model_steps),
            "tool_calls": len(result.tool_outputs),
            "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens,
            "cached_input_tokens": result.usage.cached_input_tokens,
            "reasoning_tokens": result.usage.reasoning_tokens,
            "cost_microusd": result.cost_microusd,
            "elapsed_seconds": result.elapsed_seconds,
            "retry_count": result.retry_count,
            "policy_denial": result.policy_denial,
            "structured_output": structured_output,
        },
    )

    if result.stop_reason == StopReason.CANCELLED:
        store.acknowledge_cancellation(run_id, worker_id)
        return AgentExecutionEvidence(result, (), None, None)

    if result.stop_reason not in {
        StopReason.COMPLETED,
        StopReason.NEEDS_APPROVAL,
    }:
        store.transition(
            run_id,
            RunState.EXECUTING,
            RunState.FAILED,
            "worker",
            {"reason": result.stop_reason},
            lease_owner=worker_id,
        )
        return AgentExecutionEvidence(result, (), None, None)

    if structured_output and structured_output.get("status") == "blocked":
        store.transition(
            run_id,
            RunState.EXECUTING,
            RunState.FAILED,
            "worker",
            {
                "reason": "agent_blocked",
                "summary": str(structured_output.get("summary", ""))[:512],
            },
            lease_owner=worker_id,
        )
        return AgentExecutionEvidence(result, (), None, None)

    diff = controller.diff(sandbox_id)
    if not diff.changed_paths:
        if structured_output and structured_output.get("status") == "needs_approval":
            store.transition(
                run_id,
                RunState.EXECUTING,
                RunState.FAILED,
                "worker",
                {"reason": "approval_requested_without_patch"},
                lease_owner=worker_id,
            )
            return AgentExecutionEvidence(result, (), diff.diff_hash, diff.artifact)
        store.transition(
            run_id,
            RunState.EXECUTING,
            RunState.COMPLETED,
            "worker",
            {"reason": "agent_completed_without_changes"},
            lease_owner=worker_id,
        )
        return AgentExecutionEvidence(result, (), diff.diff_hash, diff.artifact)

    store.append_event(
        run_id,
        "patch_ready",
        "worker",
        {
            "diff_hash": diff.diff_hash,
            "patch_artifact": diff.artifact.to_dict(),
            "changed_paths": list(diff.changed_paths),
        },
    )
    return AgentExecutionEvidence(
        result, diff.changed_paths, diff.diff_hash, diff.artifact
    )
