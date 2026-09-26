from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

import forge_api.main as api_main
from forge_agent_core import RunStore


class ApiRunContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.repository = self.root / "repository"
        self.repository.mkdir()
        self.original_store = api_main.store
        api_main.store = RunStore(self.root / "forge.db")

    def tearDown(self) -> None:
        api_main.store = self.original_store
        self.temporary_directory.cleanup()

    def test_api_persists_normalized_objective_and_default_budgets(self) -> None:
        created = api_main.create_run(
            api_main.CreateRunRequest(
                repository_path=str(self.repository),
                objective="  Fix the parser and add a regression test.  ",
            )
        )

        self.assertEqual(
            created["objective"],
            "Fix the parser and add a regression test.",
        )
        self.assertEqual(created["budget_total_tokens"], 50_000)
        self.assertEqual(created["budget_cost_microusd"], 1_000_000)
        self.assertEqual(created["budget_patch_attempts"], 5)

    def test_api_rejects_empty_objective_and_unsafe_budget(self) -> None:
        with self.assertRaises(ValidationError):
            api_main.CreateRunRequest(
                repository_path=str(self.repository), objective="   "
            )
        with self.assertRaises(ValidationError):
            api_main.CreateRunRequest(
                repository_path=str(self.repository),
                objective="Inspect the repository.",
                budgets={"cost_microusd": 100_000_001},
            )


if __name__ == "__main__":
    unittest.main()
