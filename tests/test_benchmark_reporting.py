from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from forge_evaluation import (
    BenchmarkRecord,
    BenchmarkSplit,
    BenchmarkVerdict,
    load_benchmark_jsonl,
    summarize_benchmark,
)


ROOT = Path(__file__).resolve().parents[1]


class BenchmarkReportingTests(unittest.TestCase):
    def record(
        self,
        task_id: str,
        *,
        category: str = "backend",
        verdict: BenchmarkVerdict = BenchmarkVerdict.SOLVED,
        failure_code: str | None = None,
        regression_free: bool = True,
        patch_attempts: int = 1,
        duration_ms: int = 1_000,
        cost_microusd: int = 100_000,
        tool_calls: int = 5,
        task_split: BenchmarkSplit = BenchmarkSplit.DEVELOPMENT,
    ) -> BenchmarkRecord:
        return BenchmarkRecord(
            schema_version=1,
            benchmark_version="forge-private-v1",
            experiment_id="tool-loop-v1",
            configuration_digest="a" * 64,
            code_revision="b" * 40,
            task_id=task_id,
            task_version="task-v1",
            task_split=task_split,
            category=category,
            language="python",
            run_id=f"run-{task_id}",
            task_manifest_digest="d" * 64,
            evaluation_manifest_digest="c" * 64,
            verdict=verdict,
            failure_code=failure_code,
            regression_free=regression_free,
            patch_attempts=patch_attempts,
            duration_ms=duration_ms,
            cost_microusd=cost_microusd,
            tool_calls=tool_calls,
        )

    def test_private_benchmark_task_identifier_is_supported(self) -> None:
        record = self.record("TASK-001")

        self.assertEqual(record.task_id, "TASK-001")

    def test_summary_separates_solver_infrastructure_and_task_failures(self) -> None:
        records = (
            self.record("task-a", category="frontend"),
            self.record(
                "task-b",
                category="backend",
                patch_attempts=2,
                duration_ms=2_000,
                cost_microusd=200_000,
                tool_calls=8,
            ),
            self.record(
                "task-c",
                category="api",
                verdict=BenchmarkVerdict.UNSOLVED,
                failure_code="check_failed",
                regression_free=False,
                duration_ms=3_000,
                cost_microusd=300_000,
            ),
            self.record(
                "task-d",
                category="data",
                verdict=BenchmarkVerdict.INFRA_INVALID,
                failure_code="runner_crashed",
                regression_free=False,
                duration_ms=4_000,
                cost_microusd=400_000,
            ),
            self.record(
                "task-e",
                category="test",
                verdict=BenchmarkVerdict.TASK_INVALID,
                failure_code="flaky_task",
                regression_free=False,
                duration_ms=0,
                cost_microusd=0,
                tool_calls=0,
            ),
        )

        summary = summarize_benchmark(reversed(records))
        value = summary.to_dict()

        self.assertEqual(
            value["counts"],
            {
                "records": 5,
                "valid": 4,
                "scored": 3,
                "solved": 2,
                "infrastructure_invalid": 1,
                "task_invalid": 1,
            },
        )
        self.assertEqual(value["metrics"]["verified_solve_rate"]["rate"], 0.666667)
        self.assertEqual(value["metrics"]["first_attempt_solve_rate"]["rate"], 0.333333)
        self.assertEqual(value["metrics"]["regression_free_rate"]["rate"], 0.666667)
        self.assertEqual(value["metrics"]["infrastructure_failure_rate"]["rate"], 0.25)
        self.assertEqual(value["metrics"]["duration_ms"], {"median": 2500.0, "p95": 4000})
        self.assertEqual(value["metrics"]["cost_per_attempted_task_microusd"], 250000.0)
        self.assertEqual(value["metrics"]["cost_per_verified_solve_microusd"], 500000.0)
        self.assertEqual(
            value["failure_distribution"],
            {"check_failed": 1, "flaky_task": 1, "runner_crashed": 1},
        )
        self.assertFalse(value["release_ready"])
        self.assertIn("Verified solve rate: 66.7%", summary.to_markdown())
        self.assertEqual(summary.digest, summarize_benchmark(records).digest)

    def test_twenty_balanced_valid_tasks_satisfy_release_gates(self) -> None:
        categories = ("frontend", "backend", "api", "data", "test")
        records = [
            self.record(
                f"task-{index:02d}",
                category=categories[index % 5],
                task_split=(
                    BenchmarkSplit.DEVELOPMENT
                    if index < 10
                    else BenchmarkSplit.HOLDOUT
                ),
            )
            for index in range(20)
        ]

        summary = summarize_benchmark(records).to_dict()

        self.assertTrue(summary["release_ready"])
        self.assertTrue(all(gate["passed"] for gate in summary["release_gates"]))
        self.assertEqual(summary["metrics"]["verified_solve_rate"]["rate"], 1.0)

        unbalanced = [
            replace(record, category="frontend") if index >= 5 else record
            for index, record in enumerate(records)
        ]
        unbalanced_gates = {
            gate["gate"]: gate["passed"]
            for gate in summarize_benchmark(unbalanced).to_dict()["release_gates"]
        }
        self.assertTrue(unbalanced_gates["required-category-coverage"])
        self.assertFalse(unbalanced_gates["balanced-category-composition"])

    def test_records_are_strict_versioned_and_configuration_consistent(self) -> None:
        with self.assertRaisesRegex(ValueError, "solved records"):
            self.record("task-a", failure_code="should_not_exist")
        with self.assertRaisesRegex(ValueError, "non-solved records"):
            self.record("task-a", verdict=BenchmarkVerdict.UNSOLVED)
        with self.assertRaisesRegex(ValueError, "benchmark taxonomy"):
            self.record("task-a", category="miscellaneous")
        with self.assertRaisesRegex(ValueError, "unsupported benchmark record schema"):
            replace(self.record("task-a"), schema_version=True)
        with self.assertRaisesRegex(ValueError, "benchmark split taxonomy"):
            replace(self.record("task-a"), task_split="development")
        with self.assertRaisesRegex(ValueError, "benchmark verdict taxonomy"):
            replace(self.record("task-a"), verdict="solved")
        with self.assertRaisesRegex(ValueError, "one record per task"):
            summarize_benchmark((self.record("task-a"), self.record("task-a")))

        other = replace(self.record("task-b"), configuration_digest="d" * 64)
        with self.assertRaisesRegex(ValueError, "configuration_digest"):
            summarize_benchmark((self.record("task-a"), other))

    def test_jsonl_loader_reports_line_and_rejects_extra_fields(self) -> None:
        first = self.record("task-a").to_dict()
        schema = json.loads(
            (ROOT / "packages" / "contracts" / "benchmark-record.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(set(schema["required"]), set(first))
        self.assertEqual(
            set(schema["properties"]["verdict"]["enum"]),
            {item.value for item in BenchmarkVerdict},
        )
        loaded = load_benchmark_jsonl((json.dumps(first), ""))
        self.assertEqual(loaded, (self.record("task-a"),))

        invalid = {**first, "unversioned_note": "not allowed"}
        with self.assertRaisesRegex(ValueError, "line 2.*unknown"):
            load_benchmark_jsonl((json.dumps(first), json.dumps(invalid)))
        with self.assertRaisesRegex(ValueError, "line 1 is not valid JSON"):
            load_benchmark_jsonl(("{broken",))

    def test_command_builds_standalone_json_and_markdown_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            source = directory / "records.jsonl"
            json_output = directory / "summary.json"
            markdown_output = directory / "card.md"
            source.write_text(
                json.dumps(self.record("task-a").to_dict()) + "\n",
                encoding="utf-8",
            )

            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "build-benchmark-report.py"),
                    str(source),
                    "--json-output",
                    str(json_output),
                    "--markdown-output",
                    str(markdown_output),
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

            artifact = json.loads(json_output.read_text(encoding="utf-8"))
            self.assertRegex(artifact["summary_digest"], r"^[0-9a-f]{64}$")
            self.assertEqual(artifact["counts"]["solved"], 1)
            self.assertIn("# Forge Benchmark Card", markdown_output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
