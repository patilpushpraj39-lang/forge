from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from forge_agent_core.run_store import RunState, RunStore
from forge_sandbox_controller import (
    LocalSandboxController,
    SandboxNotFoundError,
)
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

    def test_expired_lease_is_recovered_by_another_worker(self) -> None:
        store = RunStore(self.database)
        created = store.create_run(str(self.repository))
        first_claim = store.claim_next_run("worker-one", lease_seconds=0.05)
        self.assertIsNotNone(first_claim)

        time.sleep(0.08)
        recovered = store.claim_next_run("worker-two", lease_seconds=1)

        self.assertIsNotNone(recovered)
        assert recovered is not None
        self.assertEqual(recovered["run_id"], created["run_id"])
        self.assertEqual(recovered["state"], RunState.SNAPSHOTTING)
        self.assertEqual(recovered["attempt"], 2)
        self.assertEqual(recovered["lease_owner"], "worker-two")
        events = store.list_events(str(created["run_id"]))
        self.assertEqual(events[-1]["event_type"], "lease_recovered")
        self.assertEqual(events[-1]["payload"]["previous_worker_id"], "worker-one")

    def test_active_command_heartbeats_prevent_duplicate_claim(self) -> None:
        store = RunStore(self.database)
        created = store.create_run(str(self.repository))
        run_id = str(created["run_id"])
        worker = threading.Thread(
            target=run_once,
            kwargs={
                "store": store,
                "worker_id": "worker-heartbeat",
                "command": (sys.executable, "-c", "import time; time.sleep(0.5)"),
                "timeout_seconds": 2,
                "lease_seconds": 0.15,
            },
            daemon=True,
        )
        worker.start()

        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            event_types = [
                event["event_type"] for event in store.list_events(run_id)
            ]
            if "command_started" in event_types:
                break
            time.sleep(0.01)
        else:
            self.fail("worker did not start the command")

        time.sleep(0.22)
        self.assertIsNone(store.claim_next_run("duplicate-worker", lease_seconds=1))
        worker.join(timeout=3)

        self.assertFalse(worker.is_alive())
        completed = store.get_run(run_id)
        self.assertEqual(completed["state"], RunState.COMPLETED)
        self.assertEqual(completed["attempt"], 1)

    def test_active_command_is_terminated_after_cancellation(self) -> None:
        store = RunStore(self.database)
        created = store.create_run(str(self.repository))
        run_id = str(created["run_id"])
        long_command = (sys.executable, "-c", "import time; time.sleep(10)")
        worker = threading.Thread(
            target=run_once,
            kwargs={
                "store": store,
                "worker_id": "worker-cancel",
                "command": long_command,
                "timeout_seconds": 5,
                "lease_seconds": 0.3,
            },
            daemon=True,
        )
        worker.start()

        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            event_types = [
                event["event_type"] for event in store.list_events(run_id)
            ]
            if "command_started" in event_types:
                break
            time.sleep(0.01)
        else:
            self.fail("worker did not start the command")

        cancellation_started = time.monotonic()
        store.request_cancellation(run_id)
        worker.join(timeout=3)

        self.assertFalse(worker.is_alive())
        self.assertLess(time.monotonic() - cancellation_started, 2.5)
        self.assertEqual(store.get_run(run_id)["state"], RunState.CANCELLED)
        event_types = [event["event_type"] for event in store.list_events(run_id)]
        self.assertIn("cancellation_requested", event_types)
        self.assertIn("command_cancelled", event_types)
        self.assertEqual(event_types[-1], "workspace_destroyed")

    def test_command_timeout_fails_run_and_releases_lease(self) -> None:
        store = RunStore(self.database)
        created = store.create_run(str(self.repository))
        slow_command = (sys.executable, "-c", "import time; time.sleep(2)")

        self.assertTrue(
            run_once(
                store,
                worker_id="worker-timeout",
                command=slow_command,
                timeout_seconds=0.1,
                lease_seconds=1,
            )
        )

        run = store.get_run(str(created["run_id"]))
        self.assertEqual(run["state"], RunState.FAILED)
        self.assertIsNone(run["lease_owner"])
        events = store.list_events(str(created["run_id"]))
        self.assertIn("command_timed_out", [event["event_type"] for event in events])

    def test_local_sandbox_uses_opaque_handle_and_is_destroyed(self) -> None:
        controller = LocalSandboxController()
        sandbox = controller.create(self.repository)

        self.assertNotIn(str(self.root), sandbox.sandbox_id)
        result = controller.execute(
            sandbox.sandbox_id,
            (sys.executable, "-c", "print('sandbox-ok')"),
            timeout_seconds=1,
            should_cancel=lambda: False,
            heartbeat=lambda: None,
            heartbeat_interval_seconds=0.1,
        )
        controller.destroy(sandbox.sandbox_id)

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.output.strip(), "sandbox-ok")
        self.assertEqual(
            (self.repository / "README.md").read_text(encoding="utf-8"),
            "fixture\n",
        )
        with self.assertRaises(SandboxNotFoundError):
            controller.destroy(sandbox.sandbox_id)


if __name__ == "__main__":
    unittest.main()
