from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math
from typing import Any, Protocol


class StopReason(StrEnum):
    COMPLETED = "completed"
    TOOL_REQUESTED = "tool_requested"
    NEEDS_APPROVAL = "needs_approval"
    TOKEN_BUDGET_EXHAUSTED = "token_budget_exhausted"
    COST_BUDGET_EXHAUSTED = "cost_budget_exhausted"
    WALL_TIME_EXHAUSTED = "wall_time_exhausted"
    MODEL_STEP_BUDGET_EXHAUSTED = "model_step_budget_exhausted"
    TOOL_CALL_BUDGET_EXHAUSTED = "tool_call_budget_exhausted"
    PATCH_ATTEMPT_BUDGET_EXHAUSTED = "patch_attempt_budget_exhausted"
    POLICY_DENIED = "policy_denied"
    INVALID_OUTPUT = "invalid_output"
    PROVIDER_ERROR = "provider_error"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ContextReference:
    snapshot_hash: str
    path: str
    start_line: int
    end_line: int
    content: str

    @property
    def provenance(self) -> str:
        return (
            f"{self.snapshot_hash}:{self.path}:"
            f"{self.start_line}-{self.end_line}"
        )


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    version: str


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolOutput:
    call_id: str
    output: dict[str, Any]


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class BudgetLimits:
    total_tokens: int
    cost_microusd: int
    wall_seconds: float
    model_steps: int
    tool_calls: int
    patch_attempts: int

    def __post_init__(self) -> None:
        integer_limits = (
            self.total_tokens,
            self.cost_microusd,
            self.model_steps,
            self.tool_calls,
            self.patch_attempts,
        )
        if any(
            not isinstance(value, int)
            or isinstance(value, bool)
            or value <= 0
            for value in integer_limits
        ):
            raise ValueError("budget limits must be positive")
        if (
            not isinstance(self.wall_seconds, (int, float))
            or isinstance(self.wall_seconds, bool)
            or not math.isfinite(self.wall_seconds)
            or self.wall_seconds <= 0
        ):
            raise ValueError("wall_seconds must be positive and finite")


@dataclass(frozen=True)
class BudgetRemaining:
    total_tokens: int
    cost_microusd: int
    wall_seconds: float
    model_steps: int
    tool_calls: int
    patch_attempts: int


@dataclass(frozen=True)
class ModelStepRequest:
    run_id: str
    step_id: str
    objective: str
    context_refs: tuple[ContextReference, ...]
    allowed_tools: tuple[ToolSpec, ...]
    output_schema: dict[str, Any]
    budget: BudgetRemaining
    prompt_version: str
    tool_version: str
    idempotency_key: str
    previous_response_id: str | None = None
    tool_outputs: tuple[ToolOutput, ...] = ()


@dataclass(frozen=True)
class ModelStepResult:
    provider: str
    model: str
    response_id: str
    stop_reason: StopReason
    usage: TokenUsage
    cost_microusd: int
    prompt_version: str
    tool_version: str
    tool_calls: tuple[ToolCall, ...] = ()
    structured_output: dict[str, Any] | None = None
    output_text: str = ""
    trace_references: tuple[str, ...] = ()


class ModelRuntime(Protocol):
    def run_step(self, request: ModelStepRequest) -> ModelStepResult: ...


class TransientModelError(RuntimeError):
    pass


class PermanentModelError(RuntimeError):
    pass


@dataclass(frozen=True)
class AgentLoopResult:
    run_id: str
    stop_reason: StopReason
    model_steps: tuple[ModelStepResult, ...]
    tool_outputs: tuple[ToolOutput, ...]
    usage: TokenUsage
    cost_microusd: int
    elapsed_seconds: float
    policy_denial: str | None = None
    retry_count: int = 0
    patch_attempts: int = 0


@dataclass(frozen=True)
class RetryPolicy:
    max_provider_attempts: int = 3
    initial_delay_seconds: float = 0.25
    multiplier: float = 2.0

    def __post_init__(self) -> None:
        if self.max_provider_attempts <= 0:
            raise ValueError("max_provider_attempts must be positive")
        if self.initial_delay_seconds < 0 or self.multiplier < 1:
            raise ValueError("invalid retry timing")


@dataclass
class BudgetTracker:
    limits: BudgetLimits
    started_at: float
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    cost_microusd: int = 0
    model_steps: int = 0
    tool_calls: int = 0
    patch_attempts: int = 0

    def remaining(self, now: float) -> BudgetRemaining:
        return BudgetRemaining(
            max(0, self.limits.total_tokens - self.total_tokens),
            max(0, self.limits.cost_microusd - self.cost_microusd),
            max(0.0, self.limits.wall_seconds - (now - self.started_at)),
            max(0, self.limits.model_steps - self.model_steps),
            max(0, self.limits.tool_calls - self.tool_calls),
            max(0, self.limits.patch_attempts - self.patch_attempts),
        )

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def charge_model_step(self, result: ModelStepResult) -> None:
        self.model_steps += 1
        self.input_tokens += result.usage.input_tokens
        self.output_tokens += result.usage.output_tokens
        self.cached_input_tokens += result.usage.cached_input_tokens
        self.reasoning_tokens += result.usage.reasoning_tokens
        self.cost_microusd += result.cost_microusd

    def charge_tool_call(self, tool_name: str) -> None:
        self.tool_calls += 1
        if tool_name == "apply_patch":
            self.patch_attempts += 1

    def exhausted_reason(self, now: float) -> StopReason | None:
        remaining = self.remaining(now)
        if remaining.wall_seconds <= 0:
            return StopReason.WALL_TIME_EXHAUSTED
        if remaining.total_tokens <= 0:
            return StopReason.TOKEN_BUDGET_EXHAUSTED
        if remaining.cost_microusd <= 0:
            return StopReason.COST_BUDGET_EXHAUSTED
        if remaining.model_steps <= 0:
            return StopReason.MODEL_STEP_BUDGET_EXHAUSTED
        if remaining.tool_calls <= 0:
            return StopReason.TOOL_CALL_BUDGET_EXHAUSTED
        if remaining.patch_attempts <= 0:
            return StopReason.PATCH_ATTEMPT_BUDGET_EXHAUSTED
        return None

    def model_exhausted_reason(self, now: float) -> StopReason | None:
        remaining = self.remaining(now)
        if remaining.wall_seconds <= 0:
            return StopReason.WALL_TIME_EXHAUSTED
        if remaining.total_tokens <= 0:
            return StopReason.TOKEN_BUDGET_EXHAUSTED
        if remaining.cost_microusd <= 0:
            return StopReason.COST_BUDGET_EXHAUSTED
        if remaining.model_steps <= 0:
            return StopReason.MODEL_STEP_BUDGET_EXHAUSTED
        return None

    def tool_exhausted_reason(self, tool_name: str) -> StopReason | None:
        if self.tool_calls >= self.limits.tool_calls:
            return StopReason.TOOL_CALL_BUDGET_EXHAUSTED
        if (
            tool_name == "apply_patch"
            and self.patch_attempts >= self.limits.patch_attempts
        ):
            return StopReason.PATCH_ATTEMPT_BUDGET_EXHAUSTED
        return None

    def usage(self) -> TokenUsage:
        return TokenUsage(
            self.input_tokens,
            self.output_tokens,
            self.cached_input_tokens,
            self.reasoning_tokens,
        )
