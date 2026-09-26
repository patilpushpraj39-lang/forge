from __future__ import annotations

import math
import unittest

from forge_agent_core import BudgetLimits
from forge_agent_core.run_contract import (
    DEFAULT_RUN_BUDGETS,
    normalize_objective,
    run_budget_payload,
    validate_run_budgets,
)


class RunContractTests(unittest.TestCase):
    def test_objective_is_normalized_and_empty_input_is_rejected(self) -> None:
        self.assertEqual(normalize_objective("  Fix the defect.  "), "Fix the defect.")
        with self.assertRaises(ValueError):
            normalize_objective("   ")
        with self.assertRaises(ValueError):
            normalize_objective("x" * 10_001)

    def test_budgets_have_safe_defaults_and_hard_maxima(self) -> None:
        self.assertEqual(
            run_budget_payload(DEFAULT_RUN_BUDGETS),
            {
                "total_tokens": 50_000,
                "cost_microusd": 1_000_000,
                "wall_seconds": 600,
                "model_steps": 30,
                "tool_calls": 80,
                "patch_attempts": 5,
            },
        )
        with self.assertRaisesRegex(ValueError, "total_tokens"):
            validate_run_budgets(
                BudgetLimits(1_000_001, 1, 1, 1, 1, 1)
            )
        with self.assertRaisesRegex(ValueError, "wall_seconds"):
            BudgetLimits(1, 1, math.nan, 1, 1, 1)
        with self.assertRaisesRegex(ValueError, "budget limits"):
            BudgetLimits(True, 1, 1, 1, 1, 1)


if __name__ == "__main__":
    unittest.main()
