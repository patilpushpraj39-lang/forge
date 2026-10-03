from __future__ import annotations

import importlib.util
import hashlib
import io
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("code_workflow_checkpoint", ROOT / "scripts/check-code-workflow.py")
checkpoint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checkpoint)


class CodeWorkflowCheckpointTests(unittest.TestCase):
    def test_real_temporary_workflow_reproduces_repairs_reviews_and_restores_decisions(self):
        with tempfile.TemporaryDirectory(prefix="forge-code-check-test-") as temporary:
            report = checkpoint.exercise(Path(temporary))
        self.assertEqual(report["baseline"], "expected_regression_failed")
        self.assertEqual(report["patched_public_tests"], 3)
        self.assertEqual(report["independent_checks_passed"], 4)
        self.assertEqual([record["fixture_decision"] for record in report["fixture_decisions"]], ["approved", "rejected"])
        self.assertTrue(report["source_unchanged"])
        self.assertFalse(report["ai_used"])
        self.assertEqual(report["provider_calls"], 0)
        self.assertEqual(report["charge_microusd"], 0)
        self.assertEqual(report["github_writes"], 0)
        self.assertEqual(report["temporary_workspaces_destroyed"], 4)
        self.assertTrue(report["fresh_verification_workspace"])
        self.assertEqual(hashlib.sha256(report["patch"].encode()).hexdigest(), report["patch_sha256"])
        self.assertIn("+    return value.strip().lower()", report["patch"])
        self.assertFalse(report["security_sandbox_proven"])
        self.assertFalse(report["live_ai_quality_proven"])
        for record in report["fixture_decisions"]:
            self.assertTrue(record["record_only"] and record["restored"]
                            and record["duplicate_event_prevented"] and record["conflict_blocked"])
            self.assertEqual(record["state"], "AWAITING_APPROVAL")
        self.assertNotIn("clerk:", str(report))
        self.assertNotIn(temporary, str(report))

    def test_modified_fixture_is_rejected_before_workflow_execution(self):
        contents = checkpoint.fixture_contents()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, content in contents.items():
                (root / name).write_bytes(content)
            (root / "status.py").write_bytes(b"raise RuntimeError('must not run')\n")
            with patch.object(checkpoint, "FIXTURE", root), \
                 patch("forge_api.main.create_offline_demo_run") as create_run:
                with self.assertRaisesRegex(ValueError, "fixture content changed"):
                    checkpoint.exercise(root)
                create_run.assert_not_called()
            self.assertFalse((root / "fixture.db").exists())

    def test_symlinked_fixture_is_not_allowed(self):
        with patch.object(checkpoint.Path, "is_symlink", return_value=True):
            with self.assertRaisesRegex(ValueError, "must not redirect"):
                checkpoint.fixture_contents()

    def test_child_refuses_inherited_live_settings_before_exercising_workflow(self):
        with patch.dict(os.environ, {"FORGE_DATABASE_PATH": "fixture-private-path"}, clear=True), \
             patch.object(checkpoint, "exercise") as exercise, redirect_stdout(io.StringIO()):
            self.assertEqual(checkpoint._fixture_child(), 2)
            exercise.assert_not_called()

    def test_cli_has_no_arbitrary_repository_or_publication_option(self):
        for option in ("--repository", "--publish", "--approve", "--enable-ai"):
            with self.subTest(option=option), patch("sys.argv", ["checkpoint", option]), \
                 patch.object(checkpoint, "run_isolated") as run, redirect_stdout(io.StringIO()):
                self.assertEqual(checkpoint.main(), 2)
                run.assert_not_called()

    def test_main_scrubs_child_settings_and_removes_only_its_temporary_directory(self):
        for status in (0, 1):
            with self.subTest(status=status), patch("sys.argv", ["checkpoint"]), \
                 patch.object(checkpoint, "run_isolated", return_value=status) as run, \
                 redirect_stdout(io.StringIO()):
                self.assertEqual(checkpoint.main(), status)
                args, temporary, environment = run.call_args.args
                self.assertEqual(args[1:3], ["-I", "-B"])
                self.assertFalse(Path(temporary).exists())
                self.assertNotEqual(Path(temporary).resolve(), ROOT)
                self.assertFalse(any(key.upper().startswith(("FORGE_", "CLERK_", "OPENAI_", "AWS_", "ANTHROPIC_", "NEXT_PUBLIC_"))
                                     for key in environment))

    def test_launcher_failure_returns_safe_error_and_no_success_report(self):
        for error in (OSError("private-fixture-error"), subprocess.TimeoutExpired("private-fixture-error", 60)):
            output = io.StringIO()
            with self.subTest(error=type(error).__name__), patch("sys.argv", ["checkpoint"]), \
                 patch.object(checkpoint, "run_isolated", side_effect=error), redirect_stdout(output):
                self.assertEqual(checkpoint.main(), 1)
            self.assertNotIn("private-fixture-error", output.getvalue())
            self.assertNotIn('"baseline":', output.getvalue())

    def test_timeout_and_interrupt_stop_only_the_launched_helper(self):
        for error in (subprocess.TimeoutExpired("fixture", 60), KeyboardInterrupt()):
            process = Mock()
            process.wait.side_effect = error
            with self.subTest(error=type(error).__name__), \
                 patch.object(checkpoint.subprocess, "Popen", return_value=process) as launch, \
                 patch.object(checkpoint, "stop_fixture_process") as stop:
                with self.assertRaises(type(error)):
                    checkpoint.run_isolated(["fixture-command"], "temporary-root", {})
                stop.assert_called_once_with(process)
                process.wait.assert_called_once_with(timeout=60)
                self.assertEqual(launch.call_args.kwargs["cwd"], "temporary-root")
                self.assertEqual(launch.call_args.kwargs["start_new_session"], os.name != "nt")

    def test_exited_helper_does_not_trigger_process_kill(self):
        process = Mock()
        process.poll.return_value = 0
        with patch.object(checkpoint.subprocess, "run") as kill:
            checkpoint.stop_fixture_process(process)
        kill.assert_not_called()
        process.wait.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows process-tree cleanup")
    def test_windows_cleanup_targets_only_owned_helper_pid_and_tree(self):
        process = Mock(pid=43210)
        process.poll.return_value = None
        with patch.object(checkpoint.subprocess, "run", return_value=Mock(returncode=0)) as kill:
            checkpoint.stop_fixture_process(process)
        self.assertEqual(kill.call_args.args[0], ["taskkill", "/PID", "43210", "/T", "/F"])
        process.wait.assert_called_once_with(timeout=5)
