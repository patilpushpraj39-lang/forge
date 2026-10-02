from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import test_postgres_integration

from forge_agent_core.run_store import RunStore


class PostgresFixtureCleanupTests(unittest.TestCase):
    """Exercise fixture lifecycle with SQLite, not PostgreSQL integration behavior."""

    def _check_cleanup(self, *, fail_after_creation: bool) -> None:
        with tempfile.TemporaryDirectory(prefix="forge-fixture-cleanup-") as temporary:
            store = RunStore(Path(temporary) / "forge.db")
            tracked = Mock(wraps=store)
            created = []

            def create_run(*args, **kwargs):
                run = store.create_run(*args, **kwargs)
                created.append(run)
                return run

            tracked.create_run.side_effect = create_run
            if fail_after_creation:
                tracked.list_events.side_effect = RuntimeError("injected fixture failure")

            class FixtureCase(test_postgres_integration.PostgresIntegrationTests):
                __unittest_skip__ = False

            case = FixtureCase(
                "test_invalid_run_ids_are_not_found_without_mutating_existing_runs"
            )
            # Run just this fixture lifecycle against disposable SQLite. The real
            # PostgreSQL suite retains its database guard and normal discovery.
            case.store = tracked
            result = unittest.TestResult()
            case.run(result)

            self.assertEqual(result.testsRun, 1)
            self.assertEqual(result.skipped, [])
            self.assertEqual(result.failures, [])
            self.assertEqual(len(result.errors), int(fail_after_creation))
            self.assertEqual(len(created), 1)
            run_id = str(created[0]["run_id"])
            self.assertEqual(store.get_run(run_id)["state"], "CANCELLED")
            self.assertIsNone(store.claim_next_run("next-test-worker"))

    def test_fixture_is_unclaimable_after_success(self) -> None:
        self._check_cleanup(fail_after_creation=False)

    def test_fixture_is_unclaimable_after_failure(self) -> None:
        self._check_cleanup(fail_after_creation=True)
