from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from forge_agent_core import RunState, RunStore, SourceSnapshot
from forge_sandbox_controller import LocalArtifactStore, LocalSandboxController
from forge_worker.main import run_once


class SourceSnapshotTests(unittest.TestCase):
    def test_worker_does_not_reread_mutable_source_after_run_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            repository = root / "repository"
            repository.mkdir()
            (repository / "README.md").write_text("immutable\n", encoding="utf-8")
            controller = LocalSandboxController(
                LocalArtifactStore(root / "artifacts")
            )
            captured = controller.create(repository)
            assert captured.snapshot_artifact is not None
            source = SourceSnapshot(**captured.snapshot_artifact.to_dict())
            controller.destroy(captured.sandbox_id)
            store = RunStore(root / "forge.db")
            created = store.create_run(
                "github://octo-org/fixture@" + "a" * 40,
                "Inspect immutable input.",
                source_snapshot=source,
            )
            shutil.rmtree(repository)

            processed = run_once(
                store,
                controller=controller,
                command=(
                    sys.executable,
                    "-c",
                    "from pathlib import Path; assert Path('README.md').read_text() == 'immutable\\n'",
                ),
            )

            self.assertTrue(processed)
            self.assertEqual(
                store.get_run(str(created["run_id"]))["state"], RunState.COMPLETED
            )


if __name__ == "__main__":
    unittest.main()
