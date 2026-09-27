from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from .model_runtime import (
    AgentLoopResult,
    BudgetLimits,
    BudgetTracker,
    ContextReference,
    ModelRuntime,
    ModelStepRequest,
    ModelStepResult,
    PermanentModelError,
    ProviderErrorDetails,
    ProviderModelError,
    RetryPolicy,
    StopReason,
    ToolOutput,
    ToolSpec,
    TransientModelError,
)
from .tool_policy import ToolPolicyError, validate_tool_call


class ToolExecutor(Protocol):
    def execute(
        self, name: str, arguments: dict[str, object], idempotency_key: str
    ) -> dict[str, object]: ...


class IdempotencyLedger(Protocol):
    def get_model_step(self, key: str) -> ModelStepResult | None: ...

    def put_model_step(self, key: str, result: ModelStepResult) -> None: ...

    def get_tool_output(self, key: str) -> ToolOutput | None: ...

    def put_tool_output(self, key: str, output: ToolOutput) -> None: ...


class InMemoryIdempotencyLedger:
    def __init__(self) -> None:
        self.model_steps: dict[str, ModelStepResult] = {}
        self.tool_outputs: dict[str, ToolOutput] = {}

    def get_model_step(self, key: str) -> ModelStepResult | None:
        return self.model_steps.get(key)

    def put_model_step(self, key: str, result: ModelStepResult) -> None:
        self.model_steps.setdefault(key, result)

    def get_tool_output(self, key: str) -> ToolOutput | None:
        return self.tool_outputs.get(key)

    def put_tool_output(self, key: str, output: ToolOutput) -> None:
        self.tool_outputs.setdefault(key, output)


@dataclass(frozen=True)
class AgentLoopRequest:
    run_id: str
    objective: str
    context_refs: tuple[ContextReference, ...]
    tools: tuple[ToolSpec, ...]
    output_schema: dict[str, object]
    budgets: BudgetLimits
    prompt_version: str
    tool_version: str


class BoundedAgentLoop:
    def __init__(
        self,
        runtime: ModelRuntime,
        tool_executor: ToolExecutor,
        ledger: IdempotencyLedger,
        *,
        retry_policy: RetryPolicy | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.runtime = runtime
        self.tool_executor = tool_executor
        self.ledger = ledger
        self.retry_policy = retry_policy or RetryPolicy()
        self.clock = clock
        self.sleeper = sleeper

    def run(
        self,
        request: AgentLoopRequest,
        *,
        should_cancel: Callable[[], bool] = lambda: False,
    ) -> AgentLoopResult:
        started_at = self.clock()
        tracker = BudgetTracker(request.budgets, started_at)
        steps: list[ModelStepResult] = []
        outputs: list[ToolOutput] = []
        pending_outputs: tuple[ToolOutput, ...] = ()
        previous_response_id: str | None = None
        retry_count = 0

        while True:
            if should_cancel():
                return self._result(
                    request.run_id,
                    StopReason.CANCELLED,
                    steps,
                    outputs,
                    tracker,
                    started_at,
                    retry_count=retry_count,
                )
            exhausted = tracker.model_exhausted_reason(self.clock())
            if exhausted is not None:
                return self._result(
                    request.run_id,
                    exhausted,
                    steps,
                    outputs,
                    tracker,
                    started_at,
                    retry_count=retry_count,
                )

            step_index = len(steps) + 1
            step_id = f"model-step-{step_index}"
            model_key = self._key(
                "model",
                request.run_id,
                step_id,
                previous_response_id or "initial",
                request.prompt_version,
                request.tool_version,
                [item.output for item in pending_outputs],
            )
            model_request = ModelStepRequest(
                run_id=request.run_id,
                step_id=step_id,
                objective=request.objective,
                context_refs=request.context_refs,
                allowed_tools=request.tools,
                output_schema=request.output_schema,
                budget=tracker.remaining(self.clock()),
                prompt_version=request.prompt_version,
                tool_version=request.tool_version,
                idempotency_key=model_key,
                previous_response_id=previous_response_id,
                tool_outputs=pending_outputs,
            )
            result = self.ledger.get_model_step(model_key)
            if result is None:
                delay = self.retry_policy.initial_delay_seconds
                for attempt in range(self.retry_policy.max_provider_attempts):
                    try:
                        result = self.runtime.run_step(model_request)
                        self.ledger.put_model_step(model_key, result)
                        break
                    except ProviderModelError as error:
                        if error.details.retryable:
                            retry_count += 1
                        if (
                            error.details.retryable
                            and attempt + 1
                            < self.retry_policy.max_provider_attempts
                        ):
                            if delay:
                                self.sleeper(delay)
                            delay *= self.retry_policy.multiplier
                            continue
                        return self._result(
                            request.run_id,
                            StopReason.PROVIDER_ERROR,
                            steps,
                            outputs,
                            tracker,
                            started_at,
                            retry_count=retry_count,
                            provider_error=error.details,
                        )
                    except TransientModelError:
                        retry_count += 1
                        if attempt + 1 >= self.retry_policy.max_provider_attempts:
                            return self._result(
                                request.run_id,
                                StopReason.PROVIDER_ERROR,
                                steps,
                                outputs,
                                tracker,
                                started_at,
                                retry_count=retry_count,
                            )
                        if delay:
                            self.sleeper(delay)
                        delay *= self.retry_policy.multiplier
                    except PermanentModelError:
                        return self._result(
                            request.run_id,
                            StopReason.INVALID_OUTPUT,
                            steps,
                            outputs,
                            tracker,
                            started_at,
                            retry_count=retry_count,
                        )
            assert result is not None
            steps.append(result)
            tracker.charge_model_step(result)

            if not result.tool_calls:
                return self._result(
                    request.run_id,
                    result.stop_reason,
                    steps,
                    outputs,
                    tracker,
                    started_at,
                    retry_count=retry_count,
                )

            exhausted = tracker.model_exhausted_reason(self.clock())
            if exhausted is not None:
                return self._result(
                    request.run_id,
                    exhausted,
                    steps,
                    outputs,
                    tracker,
                    started_at,
                    retry_count=retry_count,
                )

            next_outputs: list[ToolOutput] = []
            for call in result.tool_calls:
                exhausted = tracker.tool_exhausted_reason(call.name)
                if exhausted is not None:
                    return self._result(
                        request.run_id,
                        exhausted,
                        steps,
                        outputs,
                        tracker,
                        started_at,
                        retry_count=retry_count,
                    )
                try:
                    validate_tool_call(request.tools, call)
                except ToolPolicyError as error:
                    return self._result(
                        request.run_id,
                        StopReason.POLICY_DENIED,
                        steps,
                        outputs,
                        tracker,
                        started_at,
                        policy_denial=str(error),
                        retry_count=retry_count,
                    )
                tool_key = self._key(
                    "tool",
                    request.run_id,
                    call.call_id,
                    call.name,
                    call.arguments,
                    request.tool_version,
                )
                tool_output = self.ledger.get_tool_output(tool_key)
                if tool_output is None:
                    try:
                        payload = self.tool_executor.execute(
                            call.name, call.arguments, tool_key
                        )
                    except ToolPolicyError as error:
                        return self._result(
                            request.run_id,
                            StopReason.POLICY_DENIED,
                            steps,
                            outputs,
                            tracker,
                            started_at,
                            policy_denial=str(error),
                            retry_count=retry_count,
                        )
                    tool_output = ToolOutput(call.call_id, payload)
                    self.ledger.put_tool_output(tool_key, tool_output)
                tracker.charge_tool_call(call.name)
                outputs.append(tool_output)
                next_outputs.append(tool_output)
            pending_outputs = tuple(next_outputs)
            previous_response_id = result.response_id

    def _result(
        self,
        run_id: str,
        reason: StopReason,
        steps: list[ModelStepResult],
        outputs: list[ToolOutput],
        tracker: BudgetTracker,
        started_at: float,
        *,
        policy_denial: str | None = None,
        retry_count: int = 0,
        provider_error: ProviderErrorDetails | None = None,
    ) -> AgentLoopResult:
        return AgentLoopResult(
            run_id=run_id,
            stop_reason=reason,
            model_steps=tuple(steps),
            tool_outputs=tuple(outputs),
            usage=tracker.usage(),
            cost_microusd=tracker.cost_microusd,
            elapsed_seconds=max(0.0, self.clock() - started_at),
            policy_denial=policy_denial,
            retry_count=retry_count,
            patch_attempts=tracker.patch_attempts,
            provider_error=provider_error,
        )

    @staticmethod
    def _key(kind: str, *parts: object) -> str:
        encoded = json.dumps(
            [kind, *parts], sort_keys=True, separators=(",", ":"), default=str
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
