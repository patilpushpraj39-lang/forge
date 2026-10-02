from __future__ import annotations

import os
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from forge_sandbox_controller import (
    CommandResult,
    CommandStatus,
    ContainerPolicy,
    DockerSandboxController,
    build_docker_run_command,
)


PINNED_FIXTURE_IMAGE = f"python@sha256:{'a' * 64}"
TEST_DOCKER_IMAGE = os.environ.get("FORGE_TEST_DOCKER_IMAGE")
ROOT = Path(__file__).resolve().parents[1]


class DockerPolicyCommandTests(unittest.TestCase):
    def test_execution_labels_include_scope_and_budget_plus_grace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory) / "source"
            repository.mkdir()
            controller = DockerSandboxController(
                PINNED_FIXTURE_IMAGE, cleanup_scope="deployment-one",
                cleanup_grace_seconds=30,
            )
            sandbox = controller.create(repository)
            try:
                with patch("forge_sandbox_controller.docker.time.time", return_value=100.25), patch(
                    "forge_sandbox_controller.docker._run_bounded_process",
                    return_value=CommandResult(CommandStatus.COMPLETED, 0, "ok"),
                ) as run:
                    controller.execute(
                        sandbox.sandbox_id, ("python", "-V"), timeout_seconds=2.5,
                        should_cancel=lambda: False, heartbeat=lambda: None,
                        heartbeat_interval_seconds=0.2,
                    )
                command = run.call_args.args[0]
                self.assertIn("forge.cleanup.scope=deployment-one", command)
                self.assertIn("forge.cleanup.deadline=133", command)
                self.assertEqual(command[-3:], [PINNED_FIXTURE_IMAGE, "python", "-V"])
            finally:
                controller.destroy(sandbox.sandbox_id)

    def test_invalid_cleanup_configuration_is_rejected(self) -> None:
        for options in (
            {"cleanup_scope": "*"},
            {"cleanup_grace_seconds": -1},
            {"cleanup_grace_seconds": float("nan")},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                DockerSandboxController(PINNED_FIXTURE_IMAGE, **options)

    def test_fast_command_exit_refreshes_workspace_resource_usage(self) -> None:
        for status in (CommandStatus.COMPLETED, CommandStatus.FAILED):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                repository = Path(directory) / "source"
                repository.mkdir()
                (repository / "fixture.py").write_bytes(b"fixture")
                controller = DockerSandboxController(
                    PINNED_FIXTURE_IMAGE,
                    policy=ContainerPolicy(workspace_bytes=1024),
                )
                sandbox = controller.create(repository)

                def fast_command(command, **options):
                    self.assertFalse(options["resource_exceeded"]())
                    workspace = Path(options["cwd"]) / "repository"
                    # The kernel caps the new file, but total workspace usage
                    # still exceeds the budget because the fixture exists.
                    (workspace / "large.bin").write_bytes(b"x" * 1024)
                    self.assertFalse(options["resource_exceeded"]())
                    return CommandResult(
                        status,
                        0 if status == CommandStatus.COMPLETED else 1,
                        "disk-limit",
                    )

                try:
                    with patch(
                        "forge_sandbox_controller.docker.time.monotonic",
                        return_value=1.0,
                    ), patch(
                        "forge_sandbox_controller.docker._run_bounded_process",
                        side_effect=fast_command,
                    ):
                        result = controller.execute(
                            sandbox.sandbox_id,
                            ("python", "-V"),
                            timeout_seconds=8,
                            should_cancel=lambda: False,
                            heartbeat=lambda: None,
                            heartbeat_interval_seconds=0.2,
                        )
                    self.assertEqual(result.status, CommandStatus.RESOURCE_LIMIT)
                    self.assertEqual(result.output, "disk-limit")
                    self.assertIsNone(result.exit_code)
                finally:
                    controller.destroy(sandbox.sandbox_id)

    def test_run_command_enforces_the_required_isolation_flags(self) -> None:
        policy = ContainerPolicy(
            cpu_count=0.5,
            memory_megabytes=128,
            process_limit=32,
            workspace_bytes=8 * 1024 * 1024,
            temporary_megabytes=16,
            output_bytes=4096,
        )
        command = build_docker_run_command(
            docker_binary="docker",
            image=PINNED_FIXTURE_IMAGE,
            workspace=Path("C:/forge/workspace"),
            container_name="forge-test",
            command=("python", "-V"),
            policy=policy,
        )
        rendered = " ".join(command)

        for required in (
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges=true",
            "--pids-limit 32",
            "--memory 128m",
            "--memory-swap 128m",
            "--cpus 0.5",
            "--ipc=none",
            "--init",
            "--user 65532:65532",
            "noexec,nosuid,nodev",
        ):
            self.assertIn(required, rendered)
        self.assertNotIn("--privileged", command)
        self.assertNotIn("/var/run/docker.sock", rendered)
        self.assertEqual(command.count("--mount"), 1)
        mount = command[command.index("--mount") + 1]
        self.assertTrue(mount.startswith("type=bind,src="))
        self.assertTrue(mount.endswith(",dst=/workspace"))
        self.assertNotIn(",rw", mount)
        self.assertEqual(command[-2:], ["python", "-V"])

    def test_mutable_image_tags_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            DockerSandboxController("python:3.12-slim")

    def test_machine_readable_policy_matches_controller_defaults(self) -> None:
        document = json.loads(
            (ROOT / "infra" / "policies" / "sandbox-v1.json").read_text(
                encoding="utf-8"
            )
        )
        policy = ContainerPolicy()

        self.assertEqual(document["identity"]["user"], policy.user)
        self.assertEqual(
            document["filesystem"]["workspace_bytes"],
            policy.workspace_bytes,
        )
        self.assertEqual(
            document["filesystem"]["temporary_megabytes"],
            policy.temporary_megabytes,
        )
        self.assertEqual(
            document["resources"]["cpu_count"], policy.cpu_count
        )
        self.assertEqual(
            document["resources"]["memory_megabytes"],
            policy.memory_megabytes,
        )
        self.assertEqual(
            document["resources"]["process_limit"],
            policy.process_limit,
        )
        self.assertEqual(
            document["resources"]["output_bytes"], policy.output_bytes
        )


@unittest.skipUnless(
    TEST_DOCKER_IMAGE,
    "FORGE_TEST_DOCKER_IMAGE is not configured",
)
class DockerSandboxAbuseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.repository = Path(self.temporary_directory.name) / "repository"
        self.repository.mkdir()
        (self.repository / "fixture.py").write_text(
            "print('fixture')\n", encoding="utf-8"
        )
        assert TEST_DOCKER_IMAGE is not None
        self.controller = DockerSandboxController(
            TEST_DOCKER_IMAGE,
            policy=ContainerPolicy(
                cpu_count=0.5,
                memory_megabytes=64,
                process_limit=32,
                workspace_bytes=1024 * 1024,
                temporary_megabytes=16,
                output_bytes=2048,
            ),
        )
        self.sandbox = self.controller.create(self.repository)

    def tearDown(self) -> None:
        self.controller.destroy(self.sandbox.sandbox_id)
        self.temporary_directory.cleanup()

    def execute(
        self, code: str, *, timeout_seconds: float = 8
    ):
        return self.controller.execute(
            self.sandbox.sandbox_id,
            ("python", "-c", code),
            timeout_seconds=timeout_seconds,
            should_cancel=lambda: False,
            heartbeat=lambda: None,
            heartbeat_interval_seconds=0.2,
            output_limit_bytes=2048,
        )

    def assert_status(self, result, expected: CommandStatus) -> None:
        self.assertEqual(
            result.status,
            expected,
            msg=(
                f"container command returned {result.status.value} "
                f"with exit code {result.exit_code}; output:\n{result.output}"
            ),
        )

    def test_network_secrets_runtime_socket_and_root_filesystem_are_denied(self) -> None:
        os.environ["FORGE_SECRET_CANARY"] = "must-not-cross-boundary"
        code = """
import os
import socket

assert os.environ.get("FORGE_SECRET_CANARY") is None
assert not os.path.exists("/var/run/docker.sock")
try:
    socket.create_connection(("1.1.1.1", 53), timeout=0.5)
except OSError:
    pass
else:
    raise AssertionError("network access unexpectedly succeeded")
try:
    open("/forge-host-escape", "w").write("unsafe")
except OSError:
    pass
else:
    raise AssertionError("read-only root filesystem was writable")
print("isolation-ok")
"""
        try:
            result = self.execute(code)
        finally:
            os.environ.pop("FORGE_SECRET_CANARY", None)

        self.assert_status(result, CommandStatus.COMPLETED)
        self.assertEqual(result.output.strip(), "isolation-ok")

    def test_output_disk_and_process_limits_are_bounded(self) -> None:
        started = time.monotonic()
        output = self.execute("print('x' * 1000000)")
        self.assert_status(output, CommandStatus.OUTPUT_LIMIT)
        self.assertLessEqual(len(output.output.encode("utf-8")), 2048)

        process_code = """
import subprocess
children = []
limited = False
try:
    for _ in range(200):
        children.append(subprocess.Popen(["sleep", "5"]))
except OSError:
    limited = True
finally:
    for child in children:
        child.terminate()
assert limited
print("pids-limited")
"""
        processes = self.execute(process_code)
        self.assert_status(processes, CommandStatus.COMPLETED)
        self.assertIn("pids-limited", processes.output)

        disk = self.execute(
            "open('large.bin', 'wb').write(b'x' * 2000000)"
        )
        self.assert_status(disk, CommandStatus.RESOURCE_LIMIT)
        self.assertLess(time.monotonic() - started, 12)

    def test_background_process_and_memory_abuse_end_with_the_container(self) -> None:
        background = self.execute(
            "import subprocess; "
            "subprocess.Popen(['sleep','30'], stdout=subprocess.DEVNULL, "
            "stderr=subprocess.DEVNULL); print('parent-exited')"
        )
        self.assert_status(background, CommandStatus.COMPLETED)
        self.assertEqual(background.output.strip(), "parent-exited")

        started = time.monotonic()
        memory = self.execute("value = bytearray(256 * 1024 * 1024); print(len(value))")
        self.assert_status(memory, CommandStatus.FAILED)
        self.assertLess(time.monotonic() - started, 8)


if __name__ == "__main__":
    unittest.main()
