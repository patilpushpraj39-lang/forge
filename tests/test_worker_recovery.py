from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from forge_agent_core.run_store import RunState, RunStore
from forge_sandbox_controller import LocalSandboxController, SandboxNotFoundError
from forge_sandbox_controller.local import _terminate_process
from forge_worker.main import execute_claimed_run, run_once


class WorkerRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repository = self.root / "repository"
        self.repository.mkdir()
        (self.repository / "README.md").write_text("fixture\n", encoding="utf-8")
        self.store = RunStore(self.root / "forge.db")
        self.run_id = str(self.store.create_run(str(self.repository))["run_id"])
        self.controller = LocalSandboxController()

    def reclaim(self) -> dict[str, object]:
        # Expire only this disposable fixture's lease, without a timing race.
        with self.store._connection() as connection:
            connection.execute(
                "UPDATE runs SET lease_expires_at = ? WHERE run_id = ?",
                ("2000-01-01T00:00:00+00:00", self.run_id),
            )
            connection.commit()
        recovered = self.store.claim_run(self.run_id, "worker-two", lease_seconds=5)
        self.assertIsNotNone(recovered)
        assert recovered is not None
        return recovered

    def assert_replacement_untouched(self) -> None:
        run = self.store.get_run(self.run_id)
        self.assertEqual(run["lease_owner"], "worker-two")
        self.assertEqual(run["state"], RunState.SNAPSHOTTING)
        self.assertEqual(run["attempt"], 2)
        lost = [
            event for event in self.store.list_events(self.run_id)
            if event["event_type"] == "worker_lease_lost"
        ]
        self.assertEqual(len(lost), 1)
        self.assertEqual(lost[0]["payload"], {"worker_id": "worker-one", "attempt": 1})

    def test_stale_claim_is_abandoned_before_creating_workspace(self) -> None:
        stale = self.store.claim_run(self.run_id, "worker-one")
        assert stale is not None
        self.reclaim()
        with patch.object(self.controller, "create", wraps=self.controller.create) as create:
            execute_claimed_run(
                self.store, stale, "worker-one", controller=self.controller
            )
            create.assert_not_called()
        self.assert_replacement_untouched()

    def test_active_command_stops_and_replacement_finishes_from_fresh_workspace(self) -> None:
        original_renew = self.store.renew_lease
        recovered: list[dict[str, object]] = []
        processes: list[subprocess.Popen[bytes]] = []
        original_popen = subprocess.Popen

        def capture_process(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            if kwargs.get("stdout") == subprocess.PIPE:
                processes.append(process)
            return process

        def lose_lease(run_id, worker_id, lease_seconds):
            if self.store.get_run(run_id)["state"] == RunState.EXECUTING and not recovered:
                recovered.append(self.reclaim())
            return original_renew(run_id, worker_id, lease_seconds)

        try:
            with patch.object(self.store, "renew_lease", side_effect=lose_lease), patch(
                "forge_sandbox_controller.local.subprocess.Popen", side_effect=capture_process
            ):
                self.assertTrue(run_once(
                    self.store,
                    worker_id="worker-one",
                    command=(sys.executable, "-c", "import time; time.sleep(10)"),
                    timeout_seconds=3,
                    lease_seconds=0.3,
                    controller=self.controller,
                ))
            self.assertEqual(len(processes), 1)
            self.assertIsNotNone(processes[0].poll())
            self.assertTrue(processes[0].stdout.closed)
            self.assert_replacement_untouched()
            events = self.store.list_events(self.run_id)
            self.assertNotIn("command_completed", [e["event_type"] for e in events])
            destroyed_id = events[-1]["payload"]["sandbox_id"]
            self.assertEqual(events[-1]["event_type"], "workspace_destroyed")
            with self.assertRaises(SandboxNotFoundError):
                self.controller.index_repository(destroyed_id)

            execute_claimed_run(
                self.store, recovered[0], "worker-two",
                command=(sys.executable, "-c", "print('recovered')"),
                controller=self.controller,
            )
            final = self.store.get_run(self.run_id)
            self.assertEqual(final["state"], RunState.COMPLETED)
            self.assertEqual(final["attempt"], 2)
            self.assertIsNone(final["lease_owner"])
            snapshots = [
                e["payload"]["sandbox_id"] for e in self.store.list_events(self.run_id)
                if e["event_type"] == "snapshot_ready"
            ]
            self.assertEqual(len(snapshots), 2)
            self.assertNotEqual(snapshots[0], snapshots[1])
        finally:
            for process in processes:
                if process.poll() is None:
                    _terminate_process(process)
                if process.stdout is not None:
                    process.stdout.close()

    def test_real_infrastructure_error_still_fails_run_and_cleans_workspace(self) -> None:
        error = RuntimeError("fixture controller unavailable")
        with patch.object(self.controller, "execute", side_effect=error):
            with self.assertRaises(RuntimeError) as raised:
                run_once(self.store, worker_id="worker-one", controller=self.controller)
            self.assertIs(raised.exception, error)
        final = self.store.get_run(self.run_id)
        self.assertEqual(final["state"], RunState.FAILED)
        self.assertIsNone(final["lease_owner"])
        self.assertEqual(self.store.list_events(self.run_id)[-1]["event_type"], "workspace_destroyed")

    def test_takeover_after_fast_command_cannot_record_stale_result(self) -> None:
        original_execute = self.controller.execute

        def takeover_after_command(*args, **kwargs):
            result = original_execute(*args, **kwargs)
            self.reclaim()
            return result

        with patch.object(self.controller, "execute", side_effect=takeover_after_command):
            self.assertTrue(run_once(
                self.store, worker_id="worker-one", controller=self.controller,
                command=(sys.executable, "-c", "print('finished')"),
            ))
        self.assert_replacement_untouched()
        events = self.store.list_events(self.run_id)
        self.assertNotIn("command_completed", [e["event_type"] for e in events])
        self.assertEqual(events[-1]["event_type"], "workspace_destroyed")

    def test_takeover_during_failure_transition_does_not_fail_new_attempt(self) -> None:
        original_transition = self.store.transition

        def takeover_before_failure(*args, **kwargs):
            if args[2] == RunState.FAILED:
                self.reclaim()
            return original_transition(*args, **kwargs)

        with patch.object(self.controller, "execute", side_effect=RuntimeError("fixture failure")), patch.object(
            self.store, "transition", side_effect=takeover_before_failure
        ):
            self.assertTrue(run_once(self.store, worker_id="worker-one", controller=self.controller))
        self.assert_replacement_untouched()
        self.assertEqual(self.store.list_events(self.run_id)[-1]["event_type"], "workspace_destroyed")
