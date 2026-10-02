from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock

import psycopg

from forge_agent_core.postgres_run_store import PostgresRunStore
from forge_agent_core.run_store import RunNotFoundError, RunStore


CANONICAL = "abcdef12-3456-4789-abcd-123456789abc"
INVALID_IDS = (
    "", "nonexistent", "0" * 36, CANONICAL.upper(),
    CANONICAL.replace("-", ""), "{" + CANONICAL + "}",
    "urn:uuid:" + CANONICAL, " " + CANONICAL, "x" * 10_000,
)


class PostgresRunIdTests(unittest.TestCase):
    """UUID input guards can be tested without a database or migrations."""

    def setUp(self) -> None:
        self.store = object.__new__(PostgresRunStore)
        self.store._connection = Mock(side_effect=AssertionError("unexpected database access"))

    def test_malformed_and_noncanonical_reads_raise_not_found_before_database(self) -> None:
        for run_id in INVALID_IDS:
            with self.subTest(run_id=run_id[:50]):
                with self.assertRaises(RunNotFoundError):
                    self.store.get_run(run_id)
        self.store._connection.assert_not_called()

    def test_malformed_event_reads_and_targeted_claims_match_sqlite(self) -> None:
        with tempfile.TemporaryDirectory(prefix="forge-run-id-test-") as temporary:
            sqlite = RunStore(Path(temporary) / "forge.db")
            for run_id in INVALID_IDS:
                with self.subTest(run_id=run_id[:50]):
                    self.assertEqual(self.store.list_events(run_id), sqlite.list_events(run_id))
                    self.assertIsNone(self.store.claim_run(run_id, "fixture-worker"))
                    self.assertIsNone(sqlite.claim_run(run_id, "fixture-worker"))
                    with self.assertRaises(RunNotFoundError):
                        sqlite.get_run(run_id)
        self.store._connection.assert_not_called()

    def test_mutation_lookup_rejects_invalid_ids_before_sql(self) -> None:
        connection = Mock()
        for run_id in INVALID_IDS:
            with self.subTest(run_id=run_id[:50]):
                with self.assertRaises(RunNotFoundError):
                    self.store._require_run(connection, run_id, for_update=True)
        connection.execute.assert_not_called()

    def test_valid_missing_uuid_still_queries_the_database(self) -> None:
        connection = Mock()
        connection.execute.return_value.fetchone.return_value = None
        context = MagicMock()
        context.__enter__.return_value = connection
        self.store._connection = Mock(return_value=context)
        with self.assertRaises(RunNotFoundError):
            self.store.get_run(CANONICAL)
        connection.execute.assert_called_once_with(
            "select * from runs where run_id = %s", (CANONICAL,),
        )

    def test_database_errors_for_valid_ids_are_not_disguised_as_not_found(self) -> None:
        connection = Mock()
        connection.execute.side_effect = psycopg.OperationalError("fixture database outage")
        context = MagicMock()
        context.__enter__.return_value = connection
        self.store._connection = Mock(return_value=context)
        with self.assertRaises(psycopg.OperationalError):
            self.store.get_run(CANONICAL)
