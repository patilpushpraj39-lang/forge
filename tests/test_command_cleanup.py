from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from forge_sandbox_controller.local import _run_bounded_process, _terminate_process


class CommandCleanupTests(unittest.TestCase):
    def assert_cleanup_after_error(self, **overrides: object) -> None:
        processes: list[subprocess.Popen[bytes]] = []
        original_popen = subprocess.Popen

        def capture_process(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            if kwargs.get("stdout") == subprocess.PIPE:
                processes.append(process)
            return process

        options = {
            "timeout_seconds": 5,
            "should_cancel": lambda: False,
            "heartbeat": lambda: None,
            "heartbeat_interval_seconds": 0.01,
            "output_limit_bytes": 1024,
        }
        options.update(overrides)
        with tempfile.TemporaryDirectory() as directory:
            try:
                with patch(
                    "forge_sandbox_controller.local.subprocess.Popen",
                    side_effect=capture_process,
                ):
                    error = options.pop("expected_error")
                    with self.assertRaises(RuntimeError) as raised:
                        _run_bounded_process(
                            [sys.executable, "-c", "import time; time.sleep(60)"],
                            cwd=Path(directory),
                            **options,
                        )
                    self.assertIs(raised.exception, error)
                    self.assertEqual(len(processes), 1)
                    self.assertIsNotNone(processes[0].poll(), "command leaked after error")
                    self.assertTrue(processes[0].stdout.closed, "output pipe leaked")
            finally:
                # Clean the child before Windows tries to remove its working directory.
                for process in processes:
                    if process.poll() is None:
                        _terminate_process(process)
                    if process.stdout is not None:
                        process.stdout.close()

    def test_cancellation_check_error_stops_command(self) -> None:
        error = RuntimeError("cancellation store unavailable")
        self.assert_cleanup_after_error(
            should_cancel=Mock(side_effect=error), expected_error=error
        )

    def test_heartbeat_error_stops_command(self) -> None:
        error = RuntimeError("lease heartbeat failed")
        self.assert_cleanup_after_error(
            heartbeat=Mock(side_effect=error), expected_error=error
        )

    def test_resource_check_error_stops_command(self) -> None:
        error = RuntimeError("resource measurement failed")
        self.assert_cleanup_after_error(
            resource_exceeded=Mock(side_effect=error), expected_error=error
        )

    def test_stop_hook_error_still_stops_command(self) -> None:
        error = RuntimeError("container cleanup failed")
        hook = Mock(side_effect=error)
        self.assert_cleanup_after_error(
            should_cancel=lambda: True, forced_stop=hook, expected_error=error
        )
        hook.assert_called_once_with()

    def test_callback_error_survives_stop_hook_error(self) -> None:
        error = RuntimeError("lease heartbeat failed")
        hook = Mock(side_effect=RuntimeError("container cleanup failed"))
        self.assert_cleanup_after_error(
            heartbeat=Mock(side_effect=error), forced_stop=hook, expected_error=error
        )
        hook.assert_called_once_with()
