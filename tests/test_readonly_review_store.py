from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock

from forge_agent_core.postgres_run_store import PostgresRunStore
from forge_agent_core.readonly_review import EFFECTS, EVENT_TYPE, SCOPE, reviewer_identity


class ReadonlyReviewStoreTests(unittest.TestCase):
    """Database-free SQL-boundary regression, not PostgreSQL integration proof."""

    def test_postgres_sql_uses_schema_actor_category_and_verified_payload_identity(self):
        root = Path(__file__).resolve().parents[1]
        migration = (root / "db/migrations/0001_walking_skeleton.sql").read_text(encoding="utf-8")
        constraint = re.search(r"constraint run_events_actor_check check \((.*?)\n    \)", migration, re.S)
        self.assertIsNotNone(constraint)
        allowed = set(re.findall(r"'([^']+)'", constraint.group(1)))
        connection = MagicMock()
        inserted = []

        def execute(sql, params):
            cursor = Mock()
            if "insert into run_events(" in sql:
                self.assertIn(params[6], allowed)
                inserted.append(params)
            cursor.fetchall.return_value = []
            cursor.fetchone.return_value = {"next_sequence": 1}
            return cursor

        connection.execute.side_effect = execute
        context = MagicMock()
        context.__enter__.return_value = connection
        store = object.__new__(PostgresRunStore)
        store._connection = Mock(return_value=context)
        run_id = "abcdef12-3456-4789-abcd-123456789abc"
        run = {"state": "COMPLETED", "source_snapshot_sha256": "b" * 64,
               "base_sha": "a" * 40, "repository_owner": "octo", "repository_name": "fixture"}
        store._require_run = Mock(return_value=run)
        payload = {"scope": SCOPE, "decision": "approved", "plan_sha256": "c" * 64,
                   "snapshot_sha256": "b" * 64, "base_sha": "a" * 40, "repository": "octo/fixture",
                   "decision_key_sha256": "d" * 64, **EFFECTS}
        event = store.record_readonly_preview_decision(run_id, "clerk:fixture", payload)
        store._require_run.assert_called_once_with(connection, run_id, for_update=True)
        self.assertEqual(len(inserted), 1)
        self.assertEqual(inserted[0][6], "user")
        self.assertEqual(inserted[0][7].obj, {**payload, "actor_id": "clerk:fixture"})
        self.assertEqual(reviewer_identity(event), "clerk:fixture")
        self.assertNotIn("actor_id", payload)  # No mutation of the caller's data.
        with self.assertRaises(ValueError):
            store.record_readonly_preview_decision(run_id, "clerk:fixture", {**payload, "actor_id": "clerk:spoof"})
        self.assertEqual(len(inserted), 1)

    def test_receipt_identity_requires_valid_new_or_legacy_review_event(self):
        event = {"event_type": EVENT_TYPE, "actor": "user", "payload": {"scope": SCOPE, "actor_id": "clerk:fixture"}}
        self.assertEqual(reviewer_identity(event), "clerk:fixture")
        self.assertEqual(reviewer_identity({**event, "actor": "clerk:legacy", "payload": {"scope": SCOPE}}), "clerk:legacy")
        for invalid in (
            {**event, "actor": "worker"},
            {**event, "event_type": "approval_granted"},
            {**event, "payload": {"scope": "readonly-readme-openai-v1", "actor_id": "clerk:fixture"}},
            {**event, "payload": {"scope": SCOPE}},
            {**event, "payload": {"scope": SCOPE, "actor_id": "clerk:"}},
        ):
            with self.subTest(event=invalid), self.assertRaises(ValueError):
                reviewer_identity(invalid)
