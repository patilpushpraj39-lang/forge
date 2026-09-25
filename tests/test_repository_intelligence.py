from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from forge_repo_intelligence import (
    RepositoryIndexer,
    SnapshotMismatchError,
)
from forge_sandbox_controller import LocalSandboxController


class RepositoryIntelligenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.repository = Path(self.temporary_directory.name) / "repository"
        (self.repository / "src").mkdir(parents=True)
        (self.repository / "web").mkdir()
        (self.repository / "ignored").mkdir()
        (self.repository / ".gitignore").write_text(
            "ignored/\n*.secret\n", encoding="utf-8"
        )
        (self.repository / "pyproject.toml").write_text(
            "[project]\nname = \"fixture\"\nversion = \"0.1.0\"\n",
            encoding="utf-8",
        )
        (self.repository / "package.json").write_text(
            '{"scripts":{"test":"node --test"}}\n', encoding="utf-8"
        )
        (self.repository / "src" / "app.py").write_text(
            "from .helper import greet\n\n"
            "class Greeter:\n"
            "    def run(self):\n"
            "        return greet('Forge')\n",
            encoding="utf-8",
        )
        (self.repository / "src" / "helper.py").write_text(
            "def greet(name):\n"
            "    return f'Hello {name}'\n",
            encoding="utf-8",
        )
        (self.repository / "web" / "app.ts").write_text(
            "import { formatName } from './format';\n"
            "export function render(name: string) {\n"
            "  return formatName(name);\n"
            "}\n",
            encoding="utf-8",
        )
        (self.repository / "web" / "format.ts").write_text(
            "export const formatName = (name: string) => name.trim();\n",
            encoding="utf-8",
        )
        (self.repository / "ignored" / "answer.py").write_text(
            "SECRET = 'answer'\n", encoding="utf-8"
        )
        (self.repository / "token.secret").write_text(
            "never index me\n", encoding="utf-8"
        )
        (self.repository / "logo.bin").write_bytes(b"\x00\x01\x02")
        (self.repository / "generated.txt").write_text(
            "x" * 300, encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def build_index(self):
        return RepositoryIndexer(max_file_bytes=128).build(
            self.repository, "snapshot-fixture"
        )

    def test_manifest_is_ignore_aware_and_detects_repository_shape(self) -> None:
        repository_index = self.build_index()
        manifest = repository_index.manifest
        entries = {entry.path: entry for entry in manifest.files}

        self.assertNotIn("ignored/answer.py", entries)
        self.assertNotIn("token.secret", entries)
        self.assertIn("ignored/", manifest.ignored_paths)
        self.assertIn("token.secret", manifest.ignored_paths)
        self.assertEqual(entries["logo.bin"].exclusion_reason, "binary")
        self.assertEqual(entries["generated.txt"].exclusion_reason, "oversized")
        self.assertIn("Python", manifest.languages)
        self.assertIn("TypeScript", manifest.languages)
        self.assertEqual(
            manifest.build_systems,
            ("Python/pyproject", "Node.js/npm"),
        )
        self.assertEqual(
            manifest.test_commands,
            ("python -m pytest", "npm test"),
        )
        self.assertEqual(
            manifest.dependencies["src/app.py"], ("src/helper.py",)
        )
        self.assertEqual(
            manifest.dependencies["web/app.ts"], ("web/format.ts",)
        )

    def test_search_and_context_pack_always_include_provenance(self) -> None:
        repository_index = self.build_index()

        path_results = repository_index.search_paths("helper")
        text_results = repository_index.search_text("Hello")
        symbol_results = repository_index.search_symbols("Greeter")
        neighbors = repository_index.dependency_neighborhood("src/app.py")

        self.assertEqual(path_results[0].path, "src/helper.py")
        self.assertEqual(text_results[0].path, "src/helper.py")
        self.assertEqual(symbol_results[0].start_line, 3)
        self.assertEqual(neighbors[0].path, "src/helper.py")
        for result in [
            *path_results,
            *text_results,
            *symbol_results,
            *neighbors,
        ]:
            self.assertEqual(result.snapshot_hash, "snapshot-fixture")
            self.assertGreaterEqual(result.start_line, 1)
            self.assertIn(result.path, result.provenance)

        context = repository_index.build_context_pack(
            [*text_results, *symbol_results], budget_characters=300
        )
        self.assertLessEqual(context.used_characters, 300)
        self.assertTrue(context.items)
        self.assertTrue(
            all("snapshot-fixture:" in item.content for item in context.items)
        )
        self.assertNotIn("never index me", "".join(
            item.content for item in context.items
        ))
        self.assertNotIn("x" * 50, "".join(
            item.content for item in context.items
        ))

    def test_objective_ranking_prefers_the_relevant_source_file(self) -> None:
        repository_index = self.build_index()
        results = repository_index.rank_relevant_files(
            "The greet function should return Hello for a supplied name"
        )

        self.assertTrue(results)
        self.assertEqual(results[0].path, "src/helper.py")
        self.assertEqual(results[0].kind, "relevance")
        self.assertIn("snapshot-fixture", results[0].provenance)

    def test_context_budget_and_snapshot_integrity_are_enforced(self) -> None:
        repository_index = self.build_index()
        results = repository_index.search_text("return")
        context = repository_index.build_context_pack(
            results, budget_characters=20
        )
        self.assertEqual(context.used_characters, 0)
        self.assertTrue(context.truncated)

        (self.repository / "src" / "helper.py").write_text(
            "def changed():\n    return True\n", encoding="utf-8"
        )
        with self.assertRaises(SnapshotMismatchError):
            repository_index.search_symbols("greet")

    def test_sandbox_exposes_bounded_index_search_and_context_operations(self) -> None:
        controller = LocalSandboxController()
        sandbox = controller.create(self.repository)
        try:
            manifest = controller.index_repository(sandbox.sandbox_id)
            results = controller.search_repository(
                sandbox.sandbox_id, "Greeter", modes=("symbol",)
            )
            context = controller.build_context_pack(
                sandbox.sandbox_id, results, budget_characters=500
            )
        finally:
            controller.destroy(sandbox.sandbox_id)

        self.assertEqual(manifest.snapshot_hash, sandbox.snapshot_hash)
        self.assertEqual(results[0].path, "src/app.py")
        self.assertEqual(len(context.items), 1)


if __name__ == "__main__":
    unittest.main()
