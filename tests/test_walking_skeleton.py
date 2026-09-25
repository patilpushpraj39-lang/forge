from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from forge_agent_core.run_store import RunState, RunStore
from forge_worker.main import run_once


class WalkingSkeletonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database = self.root / "forge.db"
        self.repository = self.root / "repository"
        self.repository.mkdir()
        (self.repository / "README.md").write_text("fixture\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_run_persists_across_store_instances(self) -> None:
        first_store = RunStore(self.database)
        created = first_store.create_run(str(self.repository))

        reopened_store = RunStore(self.database)
        loaded = reopened_store.get_run(str(created["run_id"]))
        events = reopened_store.list_events(str(created["run_id"]))

        self.assertEqual(loaded["state"], RunState.CREATED)
        self.assertEqual([event["event_type"] for event in events], ["run_created"])

    def test_worker_records_snapshot_command_and_cleanup(self) -> None:
        store = RunStore(self.database)
        created = store.create_run(str(self.repository))

        self.assertTrue(run_once(store))

        completed = store.get_run(str(created["run_id"]))
        events = store.list_events(str(created["run_id"]))
        event_types = [event["event_type"] for event in events]

        self.assertEqual(completed["state"], RunState.COMPLETED)
        self.assertEqual([event["sequence"] for event in events], list(range(1, 9)))
        self.assertIn("snapshot_ready", event_types)
        self.assertIn("command_started", event_types)
        self.assertIn("command_completed", event_types)
        self.assertEqual(event_types[-1], "workspace_destroyed")
        required_event_keys = {
            "event_id",
            "run_id",
            "sequence",
            "schema_version",
            "event_type",
            "occurred_at",
            "actor",
            "payload",
        }
        for event in events:
            self.assertEqual(set(event), required_event_keys)

    def test_cancelled_run_is_not_claimed(self) -> None:
        store = RunStore(self.database)
        created = store.create_run(str(self.repository))
        cancelled = store.cancel_run(str(created["run_id"]))

        self.assertEqual(cancelled["state"], RunState.CANCELLED)
        self.assertFalse(run_once(store))


if __name__ == "__main__":
    unittest.main()
