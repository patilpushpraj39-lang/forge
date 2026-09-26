from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from forge_agent_core.agent_loop import (
    AgentLoopRequest,
    BoundedAgentLoop,
    InMemoryIdempotencyLedger,
)
from forge_agent_core.idempotency import SqliteIdempotencyLedger
from forge_agent_core.model_runtime import (
    BudgetLimits,
    ModelStepResult,
    PermanentModelError,
    RetryPolicy,
    StopReason,
    TokenUsage,
    ToolCall,
    ToolSpec,
    TransientModelError,
)
from forge_agent_core.tool_policy import ToolPolicyError


READ_FILE = ToolSpec(
    "read_file",
    "Read one repository file.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1, "maxLength": 200}
        },
        "required": ["path"],
        "additionalProperties": False,
    },
    "tools-v1",
)
APPLY_PATCH = ToolSpec(
    "apply_patch",
    "Apply one bounded patch.",
    {
        "type": "object",
        "properties": {
            "patch": {"type": "string", "minLength": 1},
            "expected_sha256": {"type": ["string", "null"]},
        },
        "required": ["patch", "expected_sha256"],
        "additionalProperties": False,
    },
    "tools-v1",
)
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["completed"]},
        "summary": {"type": "string"},
    },
    "required": ["status", "summary"],
    "additionalProperties": False,
}


def step(
    response_id: str,
    *,
    calls: tuple[ToolCall, ...] = (),
    tokens: int = 2,
    cost: int = 10,
    reason: StopReason | None = None,
) -> ModelStepResult:
    return ModelStepResult(
        provider="fixture",
        model="fixture-model",
        response_id=response_id,
        stop_reason=(
            reason
            or (StopReason.TOOL_REQUESTED if calls else StopReason.COMPLETED)
        ),
        usage=TokenUsage(tokens - 1, 1),
        cost_microusd=cost,
        prompt_version="prompt-v1",
        tool_version="tools-v1",
        tool_calls=calls,
        structured_output=(
            None if calls else {"status": "completed", "summary": "done"}
        ),
    )


class _ScriptedRuntime:
    def __init__(self, results):
        self.results = list(results)
        self.calls = 0
        self.requests = []

    def run_step(self, request):
        self.calls += 1
        self.requests.append(request)
        outcome = self.results.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _RecordingExecutor:
    def __init__(self):
        self.calls = []

    def execute(self, name, arguments, idempotency_key):
        self.calls.append((name, arguments, idempotency_key))
        return {"ok": True, "name": name}


class BoundedAgentLoopTests(unittest.TestCase):
    def request(self, **budget_overrides) -> AgentLoopRequest:
        budget_values = {
            "total_tokens": 100,
            "cost_microusd": 1_000,
            "wall_seconds": 30,
            "model_steps": 5,
            "tool_calls": 5,
            "patch_attempts": 2,
            **budget_overrides,
        }
        return AgentLoopRequest(
            run_id="run-fixture",
            objective="Fix the defect and verify it.",
            context_refs=(),
            tools=(READ_FILE, APPLY_PATCH),
            output_schema=OUTPUT_SCHEMA,
            budgets=BudgetLimits(**budget_values),
            prompt_version="prompt-v1",
            tool_version="tools-v1",
        )

    def test_tool_loop_completes_and_duplicate_delivery_is_idempotent(self) -> None:
        runtime = _ScriptedRuntime(
            [
                step(
                    "response-1",
                    calls=(ToolCall("call-1", "read_file", {"path": "app.py"}),),
                ),
                step("response-2"),
            ]
        )
        executor = _RecordingExecutor()
        ledger = InMemoryIdempotencyLedger()
        loop = BoundedAgentLoop(runtime, executor, ledger)

        first = loop.run(self.request())
        second = loop.run(self.request())

        self.assertEqual(first.stop_reason, StopReason.COMPLETED)
        self.assertEqual(second.stop_reason, StopReason.COMPLETED)
        self.assertEqual(runtime.calls, 2)
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(len(first.model_steps), 2)
        self.assertEqual(first.usage.total_tokens, 4)
        self.assertEqual(first.cost_microusd, 20)
        self.assertEqual(
            runtime.requests[1].previous_response_id, "response-1"
        )
        self.assertEqual(
            runtime.requests[1].tool_outputs[0].call_id, "call-1"
        )

    def test_duplicate_delivery_survives_ledger_recreation(self) -> None:
        runtime = _ScriptedRuntime(
            [
                step(
                    "response-1",
                    calls=(ToolCall("call-1", "read_file", {"path": "app.py"}),),
                ),
                step("response-2"),
            ]
        )
        executor = _RecordingExecutor()
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "idempotency.db"
            first_loop = BoundedAgentLoop(
                runtime,
                executor,
                SqliteIdempotencyLedger(database),
            )
            self.assertEqual(
                first_loop.run(self.request()).stop_reason,
                StopReason.COMPLETED,
            )
            recovered_loop = BoundedAgentLoop(
                runtime,
                executor,
                SqliteIdempotencyLedger(database),
            )
            self.assertEqual(
                recovered_loop.run(self.request()).stop_reason,
                StopReason.COMPLETED,
            )

        self.assertEqual(runtime.calls, 2)
        self.assertEqual(len(executor.calls), 1)

    def test_unknown_tool_is_denied_before_execution(self) -> None:
        runtime = _ScriptedRuntime(
            [
                step(
                    "response-1",
                    calls=(ToolCall("call-1", "shell", {"command": "rm"}),),
                )
            ]
        )
        executor = _RecordingExecutor()
        result = BoundedAgentLoop(
            runtime, executor, InMemoryIdempotencyLedger()
        ).run(self.request())

        self.assertEqual(result.stop_reason, StopReason.POLICY_DENIED)
        self.assertIn("not allowed", result.policy_denial or "")
        self.assertFalse(executor.calls)

    def test_executor_policy_denial_becomes_an_explicit_stop(self) -> None:
        class DeniedExecutor:
            def execute(self, name, arguments, idempotency_key):
                raise ToolPolicyError("command subcommand is not allowed")

        runtime = _ScriptedRuntime(
            [
                step(
                    "response-1",
                    calls=(
                        ToolCall("call-1", "read_file", {"path": "app.py"}),
                    ),
                )
            ]
        )
        result = BoundedAgentLoop(
            runtime, DeniedExecutor(), InMemoryIdempotencyLedger()
        ).run(self.request())

        self.assertEqual(result.stop_reason, StopReason.POLICY_DENIED)
        self.assertIn("subcommand", result.policy_denial or "")

    def test_tool_and_patch_budgets_stop_before_extra_side_effects(self) -> None:
        two_reads = _ScriptedRuntime(
            [
                step(
                    "response-1",
                    calls=(
                        ToolCall("call-1", "read_file", {"path": "one.py"}),
                        ToolCall("call-2", "read_file", {"path": "two.py"}),
                    ),
                )
            ]
        )
        executor = _RecordingExecutor()
        tool_result = BoundedAgentLoop(
            two_reads, executor, InMemoryIdempotencyLedger()
        ).run(self.request(tool_calls=1))
        self.assertEqual(
            tool_result.stop_reason, StopReason.TOOL_CALL_BUDGET_EXHAUSTED
        )
        self.assertEqual(len(executor.calls), 1)

        patch_runtime = _ScriptedRuntime(
            [
                step(
                    "response-1",
                    calls=(
                        ToolCall(
                            "patch-1",
                            "apply_patch",
                            {"patch": "first", "expected_sha256": None},
                        ),
                    ),
                ),
                step(
                    "response-2",
                    calls=(
                        ToolCall(
                            "patch-2",
                            "apply_patch",
                            {"patch": "second", "expected_sha256": None},
                        ),
                    ),
                ),
            ]
        )
        patch_executor = _RecordingExecutor()
        patch_result = BoundedAgentLoop(
            patch_runtime,
            patch_executor,
            InMemoryIdempotencyLedger(),
        ).run(self.request(patch_attempts=1))
        self.assertEqual(
            patch_result.stop_reason,
            StopReason.PATCH_ATTEMPT_BUDGET_EXHAUSTED,
        )
        self.assertEqual(patch_result.patch_attempts, 1)
        self.assertEqual(len(patch_executor.calls), 1)

    def test_model_budget_stops_before_requested_tool(self) -> None:
        runtime = _ScriptedRuntime(
            [
                step(
                    "response-1",
                    calls=(ToolCall("call-1", "read_file", {"path": "app.py"}),),
                    tokens=5,
                )
            ]
        )
        executor = _RecordingExecutor()
        result = BoundedAgentLoop(
            runtime, executor, InMemoryIdempotencyLedger()
        ).run(self.request(total_tokens=5))

        self.assertEqual(
            result.stop_reason, StopReason.TOKEN_BUDGET_EXHAUSTED
        )
        self.assertFalse(executor.calls)

    def test_transient_errors_retry_and_permanent_errors_stop(self) -> None:
        runtime = _ScriptedRuntime(
            [TransientModelError("retry"), step("response-1")]
        )
        result = BoundedAgentLoop(
            runtime,
            _RecordingExecutor(),
            InMemoryIdempotencyLedger(),
            retry_policy=RetryPolicy(
                max_provider_attempts=2,
                initial_delay_seconds=0,
            ),
        ).run(self.request())
        self.assertEqual(result.stop_reason, StopReason.COMPLETED)
        self.assertEqual(result.retry_count, 1)

        failed = BoundedAgentLoop(
            _ScriptedRuntime([PermanentModelError("invalid")]),
            _RecordingExecutor(),
            InMemoryIdempotencyLedger(),
        ).run(self.request())
        self.assertEqual(failed.stop_reason, StopReason.INVALID_OUTPUT)


if __name__ == "__main__":
    unittest.main()
