from __future__ import annotations

import json
import io
import math
import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from forge_sandbox_controller import ContainerPolicy, build_docker_run_command
from forge_sandbox_controller.recovery import (
    CONTAINER_ID, DEADLINE_LABEL, SCOPE_LABEL, DockerSandboxReaper,
    main,
)


TEST_DOCKER_IMAGE = os.environ.get("FORGE_TEST_DOCKER_IMAGE")


class SandboxReaperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.container_id = "a" * 64
        self.metadata = {
            "id": self.container_id,
            "name": f"/forge-exec-{uuid.uuid4()}",
            "labels": {
                "forge.sandbox": "true",
                SCOPE_LABEL: "test-scope",
                DEADLINE_LABEL: "100",
            },
        }
        self.commands: list[list[str]] = []

    def docker(self, command, **options):
        self.commands.append(command)
        self.assertGreater(options["timeout"], 0)
        self.assertLessEqual(options["timeout"], 10)
        if "ls" in command:
            output = self.container_id + "\n"
        elif "inspect" in command:
            output = json.dumps(self.metadata)
        else:
            output = self.container_id
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    def reap(self, *, dry_run=True, callback=None):
        with patch("forge_sandbox_controller.recovery.subprocess.run", side_effect=callback or self.docker), patch(
            "forge_sandbox_controller.recovery.time.time", return_value=200
        ):
            return DockerSandboxReaper("test-scope").reap(dry_run=dry_run)

    def test_default_is_preview_only_with_scoped_listing(self) -> None:
        report = self.reap()
        self.assertTrue(report.dry_run)
        self.assertEqual(report.eligible, [self.container_id])
        self.assertEqual(report.removed, [])
        self.assertEqual(len(self.commands), 2)
        self.assertIn("--no-trunc", self.commands[0])
        self.assertIn("label=forge.sandbox=true", self.commands[0])
        self.assertIn(f"label={SCOPE_LABEL}=test-scope", self.commands[0])

    def test_execution_removes_only_inspected_expired_full_id(self) -> None:
        report = self.reap(dry_run=False)
        self.assertEqual(report.removed, [self.container_id])
        self.assertEqual(self.commands[-1], [
            "docker", "container", "rm", "--force", self.container_id,
        ])
        self.assertNotIn(self.metadata["name"], self.commands[-1])

    def test_inspection_rejects_foreign_active_legacy_and_mislabeled_targets(self) -> None:
        original = json.dumps(self.metadata)
        cases = (
            ("label", SCOPE_LABEL, "other-scope"),
            ("label", "forge.sandbox", "false"),
            ("label", DEADLINE_LABEL, "201"),
            ("label", DEADLINE_LABEL, ""),
            ("label", DEADLINE_LABEL, "NaN"),
            ("label", DEADLINE_LABEL, "-1"),
            ("label", DEADLINE_LABEL, 100),
            ("metadata", "id", "b" * 64),
            ("metadata", "name", "/unrelated-container"),
            ("metadata", "labels", None),
        )
        for area, key, value in cases:
            with self.subTest(area=area, key=key, value=value):
                self.metadata = json.loads(original)
                target = self.metadata["labels"] if area == "label" else self.metadata
                target[key] = value
                self.commands.clear()
                report = self.reap(dry_run=False)
                self.assertEqual(report.eligible, [])
                self.assertIn(self.container_id, report.skipped)
                self.assertFalse(any("rm" in c for c in self.commands))

    def test_invalid_full_id_aborts_before_any_removal(self) -> None:
        def invalid_ids(command, **options):
            result = self.docker(command, **options)
            if "ls" in command:
                result.stdout += "short-id\n"
            return result

        with self.assertRaises(RuntimeError):
            self.reap(dry_run=False, callback=invalid_ids)
        self.assertEqual(len(self.commands), 1)

    def test_candidate_bound_aborts_before_any_removal(self) -> None:
        with patch("forge_sandbox_controller.recovery.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, stdout="\n".join(f"{i:064x}" for i in range(101)))
            with self.assertRaises(RuntimeError):
                DockerSandboxReaper("test-scope").reap(dry_run=False)
            self.assertEqual(run.call_count, 1)

    def test_listing_failure_does_not_attempt_inspection_or_removal(self) -> None:
        with patch("forge_sandbox_controller.recovery.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 1, stdout="", stderr="fixture failure")
            with self.assertRaises(RuntimeError):
                DockerSandboxReaper("test-scope").reap(dry_run=False)
            self.assertEqual(run.call_count, 1)

    def test_missing_container_is_skipped_safely(self) -> None:
        def unavailable(command, **options):
            result = self.docker(command, **options)
            if "inspect" in command:
                result.returncode = 1
            return result

        report = self.reap(dry_run=False, callback=unavailable)
        self.assertEqual(report.skipped[self.container_id], "inspection_unavailable")
        self.assertEqual(report.removed, [])

    def test_removal_failure_is_reported_and_can_be_retried(self) -> None:
        def failed_removal(command, **options):
            result = self.docker(command, **options)
            if "rm" in command:
                result.returncode = 1
            return result

        report = self.reap(dry_run=False, callback=failed_removal)
        self.assertEqual(report.removed, [])
        self.assertIn(self.container_id, report.failures)
        self.assertEqual(self.reap(dry_run=False).removed, [self.container_id])

    def test_malformed_metadata_never_triggers_removal(self) -> None:
        for text in ("not-json", "null", "[]", '{"labels":{}}'):
            with self.subTest(text=text):
                def malformed(command, **options):
                    result = self.docker(command, **options)
                    if "inspect" in command:
                        result.stdout = text
                    return result

                report = self.reap(dry_run=False, callback=malformed)
                self.assertEqual(report.removed, [])
                self.assertIn(self.container_id, report.skipped)

    def test_scope_must_be_explicit_and_valid(self) -> None:
        for scope in ("", "*", "../other", "scope\nother", "x" * 65):
            with self.subTest(scope=scope), self.assertRaises(ValueError):
                DockerSandboxReaper(scope)

    def test_ambiguous_execution_mode_cannot_remove_anything(self) -> None:
        with patch("forge_sandbox_controller.recovery.subprocess.run") as run:
            for mode in (None, 0, 1, "false"):
                with self.subTest(mode=mode), self.assertRaises(ValueError):
                    DockerSandboxReaper("test-scope").reap(dry_run=mode)
            run.assert_not_called()

    def test_interrupted_scan_preserves_completed_removals_in_report(self) -> None:
        second_id = "b" * 64

        def interrupted(command, **options):
            result = self.docker(command, **options)
            if "ls" in command:
                result.stdout += second_id + "\n"
            if command[-1] == second_id:
                raise subprocess.TimeoutExpired(command, 10)
            return result

        report = self.reap(dry_run=False, callback=interrupted)
        self.assertEqual(report.removed, [self.container_id])
        self.assertEqual(report.failures[second_id], "inspection_interrupted")

    def test_timed_out_removal_reports_unknown_outcome(self) -> None:
        def interrupted(command, **options):
            result = self.docker(command, **options)
            if "rm" in command:
                raise subprocess.TimeoutExpired(command, 10)
            return result

        report = self.reap(dry_run=False, callback=interrupted)
        self.assertEqual(report.removed, [])
        self.assertEqual(report.failures[self.container_id], "removal_outcome_unknown")

    def test_cli_requires_scope_and_defaults_to_preview(self) -> None:
        with patch("sys.argv", ["reap-sandboxes.py", "--scope", "test-scope"]), patch.object(
            DockerSandboxReaper, "reap", return_value=self.reap()
        ) as reap, redirect_stdout(io.StringIO()):
            self.assertEqual(main(), 0)
            reap.assert_called_once_with(dry_run=True)
        with patch("sys.argv", ["reap-sandboxes.py", "--execute"]), redirect_stdout(io.StringIO()), patch(
            "sys.stderr", new_callable=io.StringIO
        ), self.assertRaises(SystemExit):
            main()


@unittest.skipUnless(TEST_DOCKER_IMAGE, "FORGE_TEST_DOCKER_IMAGE is not configured")
class SandboxCrashIntegrationTests(unittest.TestCase):
    def test_hard_killed_owner_leaves_only_expired_scoped_container_eligible(self) -> None:
        scope = f"crash-test-{uuid.uuid4()}"
        created_ids: list[str] = []
        worker = None
        assert TEST_DOCKER_IMAGE is not None
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repository"
            repository.mkdir()
            (repository / "fixture.py").write_text("print('fixture')\n", encoding="utf-8")
            (root / "worker-temp").mkdir()
            worker_code = (
                "import sys,tempfile; from pathlib import Path; "
                "from forge_sandbox_controller import DockerSandboxController; "
                "tempfile.tempdir=sys.argv[4]; "
                "controller=DockerSandboxController(sys.argv[1],cleanup_scope=sys.argv[2],cleanup_grace_seconds=0); "
                "sandbox=controller.create(Path(sys.argv[3])); "
                "controller.execute(sandbox.sandbox_id,('python','-c','import time; time.sleep(60)'),"
                "timeout_seconds=4,should_cancel=lambda:False,heartbeat=lambda:None,heartbeat_interval_seconds=0.1)"
            )
            try:
                worker = subprocess.Popen(
                    [sys.executable, "-c", worker_code, TEST_DOCKER_IMAGE, scope,
                     str(repository), str(root / "worker-temp")],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                startup_deadline = time.monotonic() + 10
                while time.monotonic() < startup_deadline:
                    listed = subprocess.run([
                        "docker", "container", "ls", "--quiet", "--no-trunc",
                        "--filter", f"label={SCOPE_LABEL}={scope}",
                    ], capture_output=True, text=True, check=True, timeout=2)
                    ids = listed.stdout.split()
                    if ids:
                        self.assertEqual(len(ids), 1)
                        self.assertRegex(ids[0], r"^[0-9a-f]{64}$")
                        created_ids.append(ids[0])
                        break
                    self.assertIsNone(worker.poll(), "sandbox owner exited before crash injection")
                    time.sleep(0.05)
                else:
                    self.fail("crash fixture did not start a Docker container")
                worker.kill()  # Hard kill: the owning process cannot execute finally.
                worker.wait(timeout=3)

                # Neighbors prove active same-scope and expired foreign-scope safety.
                for neighbor_scope, deadline in (
                    (scope, math.ceil(time.time() + 60)),
                    (f"foreign-{uuid.uuid4()}", math.ceil(time.time() - 10)),
                ):
                    command = build_docker_run_command(
                        docker_binary="docker", image=TEST_DOCKER_IMAGE,
                        workspace=repository, container_name=f"forge-exec-{uuid.uuid4()}",
                        command=("python", "-c", "import time; time.sleep(60)"),
                        policy=ContainerPolicy(), cleanup_scope=neighbor_scope,
                        cleanup_deadline=deadline,
                    )
                    command.insert(2, "--detach")
                    started = subprocess.run(command, capture_output=True, text=True, check=True, timeout=10)
                    container_id = started.stdout.strip()
                    self.assertTrue(CONTAINER_ID.fullmatch(container_id))
                    created_ids.append(container_id)

                reaper = DockerSandboxReaper(scope)
                wait_deadline = time.monotonic() + 8
                while time.monotonic() < wait_deadline:
                    preview = reaper.reap()
                    if preview.eligible:
                        break
                    time.sleep(0.1)
                else:
                    self.fail("crashed container did not become cleanup-eligible")
                self.assertEqual(preview.eligible, [created_ids[0]])
                self.assertEqual(preview.removed, [])
                report = reaper.reap(dry_run=False)
                self.assertEqual(report.removed, [created_ids[0]])
                self.assertEqual(report.failures, {})
                for neighbor_id in created_ids[1:]:
                    result = subprocess.run([
                        "docker", "container", "inspect", "--format", "{{.State.Running}}", neighbor_id,
                    ], capture_output=True, text=True, check=True, timeout=5)
                    self.assertEqual(result.stdout.strip(), "true")
                self.assertEqual(reaper.reap(dry_run=False).eligible, [])
            finally:
                if worker is not None and worker.poll() is None:
                    worker.kill()
                    worker.wait(timeout=3)
                # Only IDs created by this test in its unique scope are touched.
                for container_id in created_ids:
                    subprocess.run([
                        "docker", "container", "rm", "--force", container_id,
                    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False)
