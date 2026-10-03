from __future__ import annotations

import importlib.util
import io
import os
import socket
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("controlled_access_checkpoint", ROOT / "scripts/check-controlled-access.py")
checkpoint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checkpoint)


class ControlledAccessCheckpointTests(unittest.TestCase):
    def test_child_environment_discards_live_settings_and_python_injection(self):
        environment = {"SystemRoot": "fixture-system", "PATH": "fixture-path",
                       "FORGE_DATABASE_URL": "fixture-database", "clerk_jwt_key": "fixture-key",
                       "OpenAI_API_KEY": "fixture-token", "FORGE_TEST_DATABASE_URL": "fixture-test-db",
                       "AWS_PROFILE": "fixture-profile", "ANTHROPIC_API_KEY": "fixture-token",
                       "NEXT_PUBLIC_FORGE_API_URL": "fixture-origin", "PYTHONPATH": "fixture-injection",
                       "PYTHONHOME": "fixture-home", "PYTHONSTARTUP": "fixture-startup"}
        clean = checkpoint.checkpoint_environment(environment)
        self.assertEqual(clean, {"SystemRoot": "fixture-system", "PATH": "fixture-path", "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertIn("OpenAI_API_KEY", environment)  # Parent configuration untouched.

    def test_network_guard_blocks_remote_and_user_service_connections(self):
        with checkpoint.block_network():
            for address in (("127.0.0.1", 8000), ("localhost", 3000), ("192.0.2.1", 443)):
                with self.subTest(address=address):
                    with self.assertRaises(AssertionError):
                        socket.create_connection(address)
                    with self.assertRaises(AssertionError):
                        socket.getaddrinfo(*address)
                    with socket.socket() as client:
                        with self.assertRaises(AssertionError):
                            client.connect(address)
                        with self.assertRaises(AssertionError):
                            client.connect_ex(address)
                    with socket.socket(type=socket.SOCK_DGRAM) as client:
                        with self.assertRaises(AssertionError):
                            client.sendto(b"fixture", address)

    def test_internal_socketpairs_still_work_for_windows_testclient(self):
        with checkpoint.block_network():
            first, second = socket.socketpair()
            with first, second:
                first.sendall(b"fixture")
                self.assertEqual(second.recv(7), b"fixture")

    def test_main_runs_isolated_python_in_temporary_directory_and_preserves_failure(self):
        for code in (0, 1):
            with self.subTest(code=code), patch("sys.argv", ["checkpoint"]), \
                 patch.object(checkpoint.subprocess, "run", return_value=SimpleNamespace(returncode=code)) as run, \
                 redirect_stdout(io.StringIO()):
                self.assertEqual(checkpoint.main(), code)
                command = run.call_args.args[0]
                options = run.call_args.kwargs
                self.assertEqual(command[1:3], ["-I", "-B"])
                self.assertNotEqual(Path(options["cwd"]).resolve(), ROOT)
                self.assertFalse(Path(options["cwd"]).exists())
                self.assertFalse(any(key.upper().startswith(checkpoint.REMOVED_PREFIXES) for key in options["env"]))
                self.assertEqual(options["timeout"], 60)

    def test_timeout_and_invalid_options_fail_without_echoing_details(self):
        output = io.StringIO()
        with patch("sys.argv", ["checkpoint"]), redirect_stdout(output), \
             patch.object(checkpoint.subprocess, "run", side_effect=subprocess.TimeoutExpired("private-fixture", 60)):
            self.assertEqual(checkpoint.main(), 1)
        self.assertNotIn("private-fixture", output.getvalue())
        with patch("sys.argv", ["checkpoint", "--enable"]), redirect_stdout(io.StringIO()), \
             patch.object(checkpoint.subprocess, "run") as run:
            self.assertEqual(checkpoint.main(), 2)
            run.assert_not_called()

    def test_fixture_child_refuses_live_configuration_before_importing_product(self):
        with patch.dict(os.environ, {"FORGE_API_MODE": "development"}, clear=True), \
             patch("unittest.defaultTestLoader.discover") as discover, redirect_stdout(io.StringIO()):
            self.assertEqual(checkpoint._run_fixtures(), 2)
            discover.assert_not_called()

    def test_missing_or_skipped_fixture_groups_cannot_report_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            for missing in (True, False):
                result = Mock(testsRun=1, skipped=[] if missing else [("fixture", "skipped")])
                result.wasSuccessful.return_value = True
                cases = Mock()
                cases.countTestCases.return_value = 0 if missing else 1
                with self.subTest(missing=missing), patch.dict(os.environ, {}, clear=True), \
                     patch.object(checkpoint.Path, "cwd", return_value=Path(temporary)), \
                     patch("unittest.defaultTestLoader.discover", return_value=cases), \
                     patch("unittest.TestSuite"), patch("unittest.TextTestRunner") as runner, \
                     patch.object(checkpoint.sys, "path", list(checkpoint.sys.path)), redirect_stdout(io.StringIO()):
                    runner.return_value.run.return_value = result
                    self.assertEqual(checkpoint._run_fixtures(), 1)
