from __future__ import annotations

import difflib
import tempfile
import unittest
from pathlib import Path

from forge_evaluation import (
    CheckKind,
    CheckSpec,
    EvaluationManifest,
    EvaluationRunner,
    FailureCode,
    PatchPolicy,
    Verdict,
    inspect_patch,
)
from forge_sandbox_controller import LocalArtifactStore, LocalSandboxController


def changed_file_patch(path: str, before: str, after: str) -> bytes:
    chunks = [f"diff --git a/{path} b/{path}\n"]
    chunks.extend(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="\n",
        )
    )
    return "".join(chunks).encode("utf-8")


def added_file_patch(path: str, content: str) -> bytes:
    lines = content.splitlines(keepends=True)
    chunks = [
        f"diff --git a/{path} b/{path}\n",
        "new file mode 100644\n",
    ]
    chunks.extend(
        difflib.unified_diff(
            [],
            lines,
            fromfile="/dev/null",
            tofile=f"b/{path}",
            lineterm="\n",
        )
    )
    return "".join(chunks).encode("utf-8")


class IndependentEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.repository = self.root / "repository"
        (self.repository / "tests").mkdir(parents=True)
        self.before = "def normalize(value):\n    return value\n"
        (self.repository / "app.py").write_text(
            self.before, encoding="utf-8"
        )
        (self.repository / "tests" / "test_app.py").write_text(
            "import unittest\n"
            "from app import normalize\n\n"
            "class PublicTest(unittest.TestCase):\n"
            "    def test_ascii_is_preserved(self):\n"
            "        self.assertEqual(normalize('ok'), 'ok')\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8",
        )
        self.artifacts = LocalArtifactStore(self.root / "artifacts")
        self.controller = LocalSandboxController(self.artifacts)
        sandbox = self.controller.create(self.repository)
        assert sandbox.snapshot_artifact is not None
        self.snapshot = sandbox.snapshot_artifact
        self.snapshot_hash = sandbox.snapshot_hash
        self.controller.destroy(sandbox.sandbox_id)
        self.hidden_patch = self.artifacts.put_bytes(
            added_file_patch(
                "_forge_hidden_test.py",
                "import unittest\n"
                "from app import normalize\n\n"
                "class HiddenTest(unittest.TestCase):\n"
                "    def test_normalizes_form_input(self):\n"
                "        self.assertEqual(normalize('  HELLO '), 'hello')\n\n"
                "if __name__ == '__main__':\n"
                "    unittest.main()\n",
            ),
            "text/x-diff; charset=utf-8",
        )
        self.runner = EvaluationRunner(self.controller)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def manifest(self, patch_hash: str) -> EvaluationManifest:
        return EvaluationManifest(
            schema_version=1,
            benchmark_version="seed-v1",
            task_id="NORMALIZE-001",
            task_version="1",
            fixture_version="fixture-v1",
            snapshot_hash=self.snapshot_hash,
            snapshot_artifact_hash=self.snapshot.sha256,
            patch_hash=patch_hash,
            hidden_patch_hash=self.hidden_patch.sha256,
            sandbox_image_digest="python-test@sha256:" + "0" * 64,
            prompt_version="forge-agent-v1",
            tool_version="forge-tools-v1",
            model_version="fixture-model-v1",
            grader_version="deterministic-rubric-v1",
            policy_version="patch-policy-v1",
            public_checks=(
                CheckSpec(
                    "public-tests",
                    CheckKind.PUBLIC_TEST,
                    ("python", "-m", "unittest", "discover", "-s", "tests"),
                ),
            ),
            hidden_checks=(
                CheckSpec(
                    "hidden-acceptance",
                    CheckKind.HIDDEN_TEST,
                    ("python", "-m", "unittest", "_forge_hidden_test.py"),
                ),
            ),
        )

    def test_answer_patch_passes_and_replay_has_stable_verdict(self) -> None:
        answer = self.artifacts.put_bytes(
            changed_file_patch(
                "app.py",
                self.before,
                "def normalize(value):\n    return value.strip().lower()\n",
            ),
            "text/x-diff; charset=utf-8",
        )
        manifest = self.manifest(answer.sha256)

        first = self.runner.evaluate(
            manifest,
            self.snapshot,
            answer,
            hidden_patch=self.hidden_patch,
        )
        second = self.runner.evaluate(
            manifest,
            self.snapshot,
            answer,
            hidden_patch=self.hidden_patch,
        )

        self.assertEqual(first.report.verdict, Verdict.PASSED)
        self.assertEqual(first.report.snapshot_hash, self.snapshot_hash)
        self.assertEqual(first.report.verdict_hash, second.report.verdict_hash)
        self.assertEqual(
            first.report_artifact.media_type,
            "application/vnd.forge.evaluation+json",
        )
        self.assertTrue(first.report.rubric.passed)
        self.assertEqual(
            [item.status for item in first.report.checks],
            ["passed", "passed"],
        )
        self.assertFalse((self.repository / "_forge_hidden_test.py").exists())
        self.assertEqual(
            (self.repository / "app.py").read_text(encoding="utf-8"),
            self.before,
        )

    def test_incomplete_and_unchanged_patches_fail(self) -> None:
        distractor = self.artifacts.put_bytes(
            changed_file_patch(
                "app.py",
                self.before,
                "# Normalize form input in a follow-up.\n" + self.before,
            ),
            "text/x-diff; charset=utf-8",
        )
        distracted = self.runner.evaluate(
            self.manifest(distractor.sha256),
            self.snapshot,
            distractor,
            hidden_patch=self.hidden_patch,
        )

        empty = self.artifacts.put_bytes(
            b"", "text/x-diff; charset=utf-8"
        )
        unchanged = self.runner.evaluate(
            self.manifest(empty.sha256),
            self.snapshot,
            empty,
            hidden_patch=self.hidden_patch,
        )

        self.assertEqual(distracted.report.verdict, Verdict.FAILED)
        self.assertIn(FailureCode.CHECK_FAILED, distracted.report.failure_codes)
        self.assertEqual(unchanged.report.verdict, Verdict.FAILED)
        self.assertIn(FailureCode.PATCH_EMPTY, unchanged.report.failure_codes)

    def test_hidden_test_is_absent_from_solver_snapshot(self) -> None:
        sandbox = self.controller.create_from_snapshot(self.snapshot)
        try:
            manifest = self.controller.index_repository(sandbox.sandbox_id)
            results = self.controller.search_repository(
                sandbox.sandbox_id, "forge_hidden", limit=20
            )
        finally:
            self.controller.destroy(sandbox.sandbox_id)

        self.assertFalse(
            any("_forge_hidden" in entry.path for entry in manifest.files)
        )
        self.assertEqual(results, ())

    def test_patch_policy_rejects_forbidden_and_test_paths(self) -> None:
        patch = (
            added_file_patch(".github/workflows/untrusted.yml", "name: bad\n")
            + changed_file_patch(
                "tests/test_app.py",
                (self.repository / "tests" / "test_app.py").read_text(
                    encoding="utf-8"
                ),
                "# disabled\n",
            )
        )
        _, findings = inspect_patch(patch, PatchPolicy())
        codes = {item.code for item in findings}

        self.assertIn(FailureCode.FORBIDDEN_PATH, codes)
        self.assertIn(FailureCode.PROTECTED_TEST_CHANGED, codes)


if __name__ == "__main__":
    unittest.main()
