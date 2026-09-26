from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from forge_sandbox_controller import (
    CommandStatus,
    InvalidPatchError,
    LocalArtifactStore,
    LocalSandboxController,
)


class SandboxIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.repository = self.root / "repository"
        self.repository.mkdir()
        (self.repository / "app.py").write_text(
            "def answer():\n    return 1\n", encoding="utf-8"
        )
        self.artifact_store = LocalArtifactStore(self.root / "artifacts")
        self.controller = LocalSandboxController(self.artifact_store)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    @staticmethod
    def patch() -> bytes:
        return (
            "--- a/app.py\n"
            "+++ b/app.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def answer():\n"
            "-    return 1\n"
            "+    return 42\n"
        ).encode("utf-8")

    def test_snapshot_and_patch_replay_produce_the_same_diff_hash(self) -> None:
        sandbox = self.controller.create(self.repository)
        assert sandbox.snapshot_artifact is not None
        input_patch = self.patch()
        applied = self.controller.apply_patch(
            sandbox.sandbox_id,
            input_patch,
            expected_sha256=hashlib.sha256(input_patch).hexdigest(),
        )
        first_diff = self.controller.diff(sandbox.sandbox_id)
        first_content = self.controller.read_artifact(
            first_diff.artifact, max_bytes=10_000
        )
        self.controller.destroy(sandbox.sandbox_id)

        replay = self.controller.create_from_snapshot(
            sandbox.snapshot_artifact
        )
        self.controller.apply_patch(
            replay.sandbox_id,
            first_content,
            expected_sha256=first_diff.diff_hash,
        )
        second_diff = self.controller.diff(replay.sandbox_id)
        self.controller.destroy(replay.sandbox_id)

        self.assertEqual(applied.changed_paths, ("app.py",))
        self.assertEqual(first_diff.changed_paths, ("app.py",))
        self.assertEqual(second_diff.diff_hash, first_diff.diff_hash)
        self.assertEqual(replay.snapshot_hash, sandbox.snapshot_hash)
        self.assertEqual(
            (self.repository / "app.py").read_text(encoding="utf-8"),
            "def answer():\n    return 1\n",
        )

    def test_snapshot_artifact_is_deterministic_and_survives_destroy(self) -> None:
        first = self.controller.create(self.repository)
        second = self.controller.create(self.repository)
        assert first.snapshot_artifact is not None
        assert second.snapshot_artifact is not None
        self.controller.destroy(first.sandbox_id)
        content = self.controller.read_artifact(
            first.snapshot_artifact,
            max_bytes=first.snapshot_artifact.size_bytes,
        )
        self.controller.destroy(second.sandbox_id)

        self.assertEqual(
            first.snapshot_artifact.sha256,
            second.snapshot_artifact.sha256,
        )
        self.assertEqual(
            hashlib.sha256(content).hexdigest(),
            first.snapshot_artifact.sha256,
        )

    def test_added_and_deleted_files_replay_from_generated_diff(self) -> None:
        (self.repository / "obsolete.txt").write_text(
            "remove me\n", encoding="utf-8"
        )
        sandbox = self.controller.create(self.repository)
        assert sandbox.snapshot_artifact is not None
        patch = (
            "diff --git a/new.txt b/new.txt\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/new.txt\n"
            "@@ -0,0 +1 @@\n"
            "+created\n"
            "diff --git a/obsolete.txt b/obsolete.txt\n"
            "deleted file mode 100644\n"
            "--- a/obsolete.txt\n"
            "+++ /dev/null\n"
            "@@ -1 +0,0 @@\n"
            "-remove me\n"
        ).encode("utf-8")
        self.controller.apply_patch(sandbox.sandbox_id, patch)
        generated = self.controller.diff(sandbox.sandbox_id)
        content = self.controller.read_artifact(
            generated.artifact, max_bytes=10_000
        )

        replay = self.controller.create_from_snapshot(
            sandbox.snapshot_artifact
        )
        try:
            self.controller.apply_patch(
                replay.sandbox_id,
                content,
                expected_sha256=generated.diff_hash,
            )
            replayed = self.controller.diff(replay.sandbox_id)
        finally:
            self.controller.destroy(sandbox.sandbox_id)
            self.controller.destroy(replay.sandbox_id)

        self.assertEqual(
            generated.changed_paths, ("new.txt", "obsolete.txt")
        )
        self.assertEqual(replayed.diff_hash, generated.diff_hash)

    def test_patch_path_escape_checksum_and_read_limits_are_rejected(self) -> None:
        sandbox = self.controller.create(self.repository)
        assert sandbox.snapshot_artifact is not None
        malicious = (
            "--- a/app.py\n"
            "+++ b/../outside.py\n"
            "@@ -1 +1 @@\n"
            "-safe\n"
            "+unsafe\n"
        ).encode("utf-8")
        try:
            with self.assertRaises(InvalidPatchError):
                self.controller.apply_patch(sandbox.sandbox_id, malicious)
            with self.assertRaises(InvalidPatchError):
                self.controller.apply_patch(
                    sandbox.sandbox_id,
                    self.patch(),
                    expected_sha256="0" * 64,
                )
            with self.assertRaises(ValueError):
                self.controller.read_artifact(
                    sandbox.snapshot_artifact,
                    max_bytes=sandbox.snapshot_artifact.size_bytes - 1,
                )
        finally:
            self.controller.destroy(sandbox.sandbox_id)

    def test_oversized_command_output_is_capped_and_terminated(self) -> None:
        sandbox = self.controller.create(self.repository)
        started = time.monotonic()
        try:
            result = self.controller.execute(
                sandbox.sandbox_id,
                (sys.executable, "-c", "print('x' * 1000000)"),
                timeout_seconds=5,
                should_cancel=lambda: False,
                heartbeat=lambda: None,
                heartbeat_interval_seconds=0.1,
                output_limit_bytes=1024,
            )
        finally:
            self.controller.destroy(sandbox.sandbox_id)

        self.assertEqual(result.status, "output_limit")
        self.assertTrue(result.output_truncated)
        self.assertLessEqual(len(result.output.encode("utf-8")), 1024)
        self.assertLess(time.monotonic() - started, 2)

    def test_generated_runtime_caches_do_not_enter_source_diff(self) -> None:
        sandbox = self.controller.create(self.repository)
        try:
            result = self.controller.execute(
                sandbox.sandbox_id,
                ("python", "-c", "import app"),
                timeout_seconds=5,
                should_cancel=lambda: False,
                heartbeat=lambda: None,
                heartbeat_interval_seconds=0.1,
            )
            diff = self.controller.diff(sandbox.sandbox_id)
        finally:
            self.controller.destroy(sandbox.sandbox_id)

        self.assertEqual(result.status, CommandStatus.COMPLETED)
        self.assertEqual(diff.changed_paths, ())

    def test_valid_patch_with_blank_context_applies_without_recount(self) -> None:
        source = self.repository / "pricing.ts"
        source.write_text(
            "export function price(value: number) {\n"
            "  if (value < 0) {\n"
            "    throw new RangeError('value');\n"
            "  }\n"
            "\n"
            "  return Math.round(value);\n"
            "}\n",
            encoding="utf-8",
        )
        patch = (
            "diff --git a/pricing.ts b/pricing.ts\n"
            "--- a/pricing.ts\n"
            "+++ b/pricing.ts\n"
            "@@ -3,5 +3,5 @@ export function price(value: number) {\n"
            "     throw new RangeError('value');\n"
            "   }\n"
            " \n"
            "-  return Math.round(value);\n"
            "+  return Math.round(value * 100) / 100;\n"
            " }\n"
        ).encode("utf-8")
        sandbox = self.controller.create(self.repository)
        try:
            applied = self.controller.apply_patch(sandbox.sandbox_id, patch)
            diff = self.controller.diff(sandbox.sandbox_id)
        finally:
            self.controller.destroy(sandbox.sandbox_id)

        self.assertEqual(applied.changed_paths, ("pricing.ts",))
        self.assertEqual(diff.changed_paths, ("pricing.ts",))

    def test_patch_application_cannot_discover_a_parent_git_repository(self) -> None:
        parent_repository = self.root / "parent"
        parent_repository.mkdir()
        subprocess.run(
            ["git", "init", "--quiet"],
            cwd=parent_repository,
            check=True,
        )
        previous_tempdir = tempfile.tempdir
        tempfile.tempdir = str(parent_repository)
        sandbox = None
        try:
            sandbox = self.controller.create(self.repository)
            self.controller.apply_patch(sandbox.sandbox_id, self.patch())
            diff = self.controller.diff(sandbox.sandbox_id)
        finally:
            tempfile.tempdir = previous_tempdir
            if sandbox is not None:
                self.controller.destroy(sandbox.sandbox_id)

        self.assertEqual(diff.changed_paths, ("app.py",))
        self.assertFalse((parent_repository / "app.py").exists())


if __name__ == "__main__":
    unittest.main()
