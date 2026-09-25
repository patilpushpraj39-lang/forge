from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from forge_agent_core import validate_tool_specs
from forge_sandbox_controller import LocalSandboxController
from forge_worker.tools import (
    CommandPolicyError,
    ForgeToolExecutor,
    forge_tool_specs,
)


class ForgeToolExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.repository = Path(self.temporary_directory.name) / "repository"
        self.repository.mkdir()
        (self.repository / "app.py").write_text(
            "def greet(name):\n"
            "    return f'Hello {name}'\n",
            encoding="utf-8",
        )
        self.controller = LocalSandboxController()
        self.sandbox = self.controller.create(self.repository)
        self.executor = ForgeToolExecutor(
            self.controller, self.sandbox.sandbox_id
        )

    def tearDown(self) -> None:
        self.controller.destroy(self.sandbox.sandbox_id)
        self.temporary_directory.cleanup()

    def test_tool_contracts_are_strict_and_repository_reads_have_provenance(
        self,
    ) -> None:
        specs = forge_tool_specs()
        validate_tool_specs(specs)
        names = {spec.name for spec in specs}
        self.assertEqual(
            names,
            {
                "repository_manifest",
                "search_repository",
                "read_file",
                "dependency_neighborhood",
                "execute_command",
                "apply_patch",
                "get_diff",
            },
        )

        manifest = self.executor.execute("repository_manifest", {}, "manifest")
        search = self.executor.execute(
            "search_repository",
            {"query": "greet", "modes": ["symbol"], "limit": 5},
            "search",
        )
        read = self.executor.execute(
            "read_file",
            {"path": "app.py", "start_line": 1, "end_line": 2},
            "read",
        )

        self.assertEqual(
            manifest["brief"]["snapshot_hash"], self.sandbox.snapshot_hash
        )
        self.assertEqual(search["results"][0]["path"], "app.py")
        self.assertEqual(
            read["result"]["provenance"],
            f"{self.sandbox.snapshot_hash}:app.py:1-2",
        )

    def test_command_patch_and_diff_tools_are_bounded(self) -> None:
        command = self.executor.execute(
            "execute_command",
            {
                "command": ["python", "--version"],
                "timeout_seconds": 5,
            },
            "command",
        )
        self.assertEqual(command["status"], "completed")
        self.assertEqual(command["exit_code"], 0)

        with self.assertRaises(CommandPolicyError):
            self.executor.execute(
                "execute_command",
                {
                    "command": ["npm", "install", "untrusted-package"],
                    "timeout_seconds": 5,
                },
                "denied-command",
            )

        patch = (
            "diff --git a/app.py b/app.py\n"
            "--- a/app.py\n"
            "+++ b/app.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def greet(name):\n"
            "-    return f'Hello {name}'\n"
            "+    return f'Welcome {name}'\n"
        )
        expected = hashlib.sha256(patch.encode("utf-8")).hexdigest()
        applied = self.executor.execute(
            "apply_patch",
            {"patch": patch, "expected_sha256": expected},
            "patch",
        )
        diff = self.executor.execute("get_diff", {}, "diff")

        self.assertEqual(applied["patch_hash"], expected)
        self.assertEqual(applied["changed_paths"], ["app.py"])
        self.assertEqual(diff["changed_paths"], ["app.py"])
        self.assertEqual(diff["diff_hash"], diff["artifact"]["sha256"])


if __name__ == "__main__":
    unittest.main()
