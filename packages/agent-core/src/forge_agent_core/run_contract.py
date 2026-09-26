from __future__ import annotations

from dataclasses import asdict

from .model_runtime import BudgetLimits


DEFAULT_RUN_OBJECTIVE = "Inspect the repository and report verified findings."
DEFAULT_RUN_BUDGETS = BudgetLimits(
    total_tokens=50_000,
    cost_microusd=1_000_000,
    wall_seconds=600,
    model_steps=30,
    tool_calls=80,
    patch_attempts=5,
)
MAX_RUN_BUDGETS = BudgetLimits(
    total_tokens=1_000_000,
    cost_microusd=100_000_000,
    wall_seconds=86_400,
    model_steps=1_000,
    tool_calls=10_000,
    patch_attempts=100,
)


def normalize_objective(objective: str) -> str:
    normalized = objective.strip()
    if not normalized:
        raise ValueError("objective is required")
    if len(normalized) > 10_000:
        raise ValueError("objective exceeds the 10000 character limit")
    return normalized


def validate_run_budgets(budgets: BudgetLimits) -> BudgetLimits:
    for name, maximum in asdict(MAX_RUN_BUDGETS).items():
        value = getattr(budgets, name)
        if value > maximum:
            raise ValueError(f"{name} exceeds the run safety limit")
    return budgets


def run_budget_payload(budgets: BudgetLimits) -> dict[str, int | float]:
    return {
        "total_tokens": budgets.total_tokens,
        "cost_microusd": budgets.cost_microusd,
        "wall_seconds": budgets.wall_seconds,
        "model_steps": budgets.model_steps,
        "tool_calls": budgets.tool_calls,
        "patch_attempts": budgets.patch_attempts,
    }
