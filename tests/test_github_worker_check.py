from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from forge_agent_core import RepositoryTarget, RunState, RunStore, SourceSnapshot
from forge_sandbox_controller import LocalArtifactStore, LocalSandboxController

SPEC = importlib.util.spec_from_file_location(
    "github_worker_check", Path(__file__).resolve().parents[1] / "scripts/check-github-worker.py",
)
assert SPEC and SPEC.loader
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


class GitHubWorkerCheckTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repository = self.root / "repository"
        self.repository.mkdir()
        self.readme = b"fixed smoke input\n"
        (self.repository / "README.md").write_bytes(self.readme)
        self.artifacts = self.root / "artifacts"
        self.controller = LocalSandboxController(LocalArtifactStore(self.artifacts))
        captured = self.controller.create(self.repository)
        self.snapshot = SourceSnapshot(**captured.snapshot_artifact.to_dict())
        self.controller.destroy(captured.sandbox_id)
        self.store = RunStore(self.root / "forge.db")
        self.target = RepositoryTarget("octo", "fixture", 42, "main", "a" * 40)

    def new_run(self, objective=None, *, snapshot=True, repository=True):
        return self.store.create_run(
            "github://octo/fixture@" + "a" * 40,
            checker.SMOKE_OBJECTIVE if objective is None else objective,
            repository=self.target if repository else None,
            source_snapshot=self.snapshot if snapshot else None,
        )

    def check(self, run, repository="octo/fixture"):
        return checker.check_run(self.store, self.controller, run["run_id"], repository)

    def test_only_targeted_run_completes_from_snapshot_without_credentials(self):
        earlier = self.new_run("Unrelated queued task")
        selected = self.new_run()
        # Changing the live source cannot change the captured worker input.
        (self.repository / "README.md").write_bytes(b"later changes")
        original_execute = self.controller.execute

        def protected_execute(*args, **kwargs):
            self.assertNotIn("OPENAI_API_KEY", os.environ)
            self.assertNotIn("FORGE_GITHUB_APP_PRIVATE_KEY_PATH", os.environ)
            self.assertNotIn("CLERK_SECRET_KEY", os.environ)
            return original_execute(*args, **kwargs)

        with mock.patch.dict(os.environ, {
            "OPENAI_API_KEY": "fixture-unused-key",
            "FORGE_GITHUB_APP_PRIVATE_KEY_PATH": "must-not-be-opened.pem",
            "CLERK_SECRET_KEY": "fixture-unused-secret",
        }), mock.patch.object(self.controller, "execute", side_effect=protected_execute):
            result = self.check(selected)
            self.assertEqual(os.environ["OPENAI_API_KEY"], "fixture-unused-key")
        self.assertEqual(result["state"], "COMPLETED")
        self.assertEqual(self.store.get_run(earlier["run_id"])["state"], RunState.CREATED)
        events = self.store.list_events(selected["run_id"])
        completed = next(event for event in events if event["event_type"] == "command_completed")
        output = json.loads(completed["payload"]["output"])
        self.assertEqual(output["sha256"], hashlib.sha256(self.readme).hexdigest())
        self.assertEqual(output["bytes"], len(self.readme))
        self.assertIn("workspace_destroyed", [event["event_type"] for event in events])
        self.assertNotIn("model_call_started", [event["event_type"] for event in events])
        self.assertIsNone(self.store.get_run(selected["run_id"])["evaluated_patch_hash"])

    def test_wrong_repository_and_task_are_rejected_before_claim(self):
        for objective, repository in ((None, "octo/other"), ("Fix an actual issue", "octo/fixture")):
            run = self.new_run(objective)
            with self.assertRaises(ValueError):
                self.check(run, repository)
            self.assertEqual(self.store.get_run(run["run_id"])["state"], RunState.CREATED)
            self.assertEqual(self.store.get_run(run["run_id"])["attempt"], 0)

    def test_cancelled_started_and_completed_runs_are_not_reused(self):
        cancelled = self.new_run()
        self.store.request_cancellation(cancelled["run_id"])
        started = self.new_run()
        self.store.claim_run(started["run_id"], "another-worker")
        completed = self.new_run()
        self.check(completed)
        for run in (cancelled, started, completed):
            with self.assertRaises(ValueError):
                self.check(run)

    def test_missing_snapshot_or_repository_is_rejected_before_claim(self):
        for run in (self.new_run(snapshot=False), self.new_run(repository=False)):
            with self.assertRaises((ValueError, TypeError)):
                self.check(run)
            self.assertEqual(self.store.get_run(run["run_id"])["attempt"], 0)

    def test_invalid_run_id_is_rejected_without_selection(self):
        with self.assertRaises(ValueError):
            checker.check_run(self.store, self.controller, "not-a-uuid", "octo/fixture")

    def test_corrupt_snapshot_fails_and_never_executes_command(self):
        run = self.new_run()
        artifact_path = self.artifacts / "sha256" / self.snapshot.sha256[:2] / self.snapshot.sha256
        artifact_path.write_bytes(b"corrupted")
        with mock.patch.object(self.controller, "execute") as execute:
            with self.assertRaises(Exception):
                self.check(run)
            execute.assert_not_called()
        self.assertEqual(self.store.get_run(run["run_id"])["state"], RunState.FAILED)

    def test_missing_readme_fails_without_model_or_publication(self):
        (self.repository / "README.md").unlink()
        captured = self.controller.create(self.repository)
        self.snapshot = SourceSnapshot(**captured.snapshot_artifact.to_dict())
        self.controller.destroy(captured.sandbox_id)
        run = self.new_run()
        with self.assertRaisesRegex(ValueError, "did not complete"):
            self.check(run)
        self.assertEqual(self.store.get_run(run["run_id"])["state"], RunState.FAILED)

    def test_profiles_storage_backends_and_path_escape_are_rejected(self):
        base = {"FORGE_DATABASE_PATH": "forge.db", "FORGE_ARTIFACT_PATH": "artifacts"}
        self.assertEqual(checker.local_paths(base, self.root), (self.root / "forge.db", self.artifacts))
        for unsafe in (
            {"FORGE_API_MODE": "controlled"}, {"FORGE_DATABASE_URL": "postgresql://unused"},
            {"FORGE_ARTIFACT_BACKEND": "s3"}, {"FORGE_SANDBOX_BACKEND": "docker"},
            {"FORGE_DATABASE_PATH": "../outside.db"}, {"FORGE_ARTIFACT_PATH": "."},
        ):
            with self.subTest(config=unsafe), self.assertRaises(ValueError):
                checker.local_paths({**base, **unsafe}, self.root)

    def test_environment_is_restored_even_on_failure(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "fixture-unused-key", "UNRELATED_SECRET": "private"}):
            with self.assertRaises(RuntimeError):
                with checker.credential_free_environment():
                    self.assertNotIn("UNRELATED_SECRET", os.environ)
                    raise RuntimeError("fixture")
            self.assertEqual(os.environ["OPENAI_API_KEY"], "fixture-unused-key")
            self.assertEqual(os.environ["UNRELATED_SECRET"], "private")

    def test_cli_help_does_not_read_configuration_or_select_run(self):
        with mock.patch.object(checker, "dotenv_values") as config, \
                mock.patch.object(checker, "check_run") as select, \
                redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as result:
                checker.main(["--help"])
        self.assertEqual(result.exception.code, 0)
        config.assert_not_called()
        select.assert_not_called()

    def test_cli_uses_local_settings_without_loading_credentials(self):
        run = self.new_run()
        (self.root / ".env.reviewer.local").write_text(
            "FORGE_DATABASE_PATH=forge.db\nFORGE_ARTIFACT_PATH=artifacts\n"
            "FORGE_GITHUB_APP_PRIVATE_KEY_PATH=must-not-be-opened.pem\n"
            "OPENAI_API_KEY=fixture-unused-key\n", encoding="utf-8",
        )
        output = io.StringIO()
        with mock.patch.object(checker, "ROOT", self.root), redirect_stdout(output):
            result = checker.main(["--run-id", run["run_id"], "--repository", "octo/fixture"])
        self.assertEqual(result, 0)
        self.assertIn("COMPLETED", output.getvalue())
        self.assertNotIn("fixture-unused-key", output.getvalue())
        self.assertNotIn("must-not-be-opened", output.getvalue())


if __name__ == "__main__":
    unittest.main()
