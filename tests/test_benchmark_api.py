from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import forge_api.main as api_main
from forge_evaluation import BenchmarkRecord, BenchmarkSplit, BenchmarkVerdict


class BenchmarkApiTests(unittest.TestCase):
    def test_dashboard_allows_both_local_development_origins(self) -> None:
        client = TestClient(api_main.app)

        for origin in ("http://localhost:3000", "http://127.0.0.1:3000"):
            response = client.options(
                "/benchmarks/latest",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "GET",
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["access-control-allow-origin"], origin)

    def record(
        self,
        task_id: str,
        *,
        category: str,
        task_split: BenchmarkSplit,
        verdict: BenchmarkVerdict,
    ) -> BenchmarkRecord:
        solved = verdict == BenchmarkVerdict.SOLVED
        return BenchmarkRecord(
            schema_version=1,
            benchmark_version="synthetic-dashboard-v1",
            experiment_id="tool-loop-v1",
            configuration_digest="a" * 64,
            code_revision="b" * 40,
            task_id=task_id,
            task_version="task-v1",
            task_split=task_split,
            category=category,
            language="python",
            run_id=f"run-{task_id}",
            task_manifest_digest="c" * 64,
            evaluation_manifest_digest="d" * 64,
            verdict=verdict,
            failure_code=None if solved else "check_failed",
            regression_free=solved,
            patch_attempts=1,
            duration_ms=10_000,
            cost_microusd=100_000,
            tool_calls=5,
        )

    def write_records(self, directory: Path) -> Path:
        path = directory / "records.jsonl"
        records = (
            self.record(
                "backend-fix-001",
                category="backend",
                task_split=BenchmarkSplit.HOLDOUT,
                verdict=BenchmarkVerdict.SOLVED,
            ),
            self.record(
                "frontend-fix-001",
                category="frontend",
                task_split=BenchmarkSplit.DEVELOPMENT,
                verdict=BenchmarkVerdict.UNSOLVED,
            ),
        )
        path.write_text(
            "".join(json.dumps(item.to_dict()) + "\n" for item in records),
            encoding="utf-8",
        )
        return path

    def test_dashboard_returns_recomputed_source_backed_views(self) -> None:
        client = TestClient(api_main.app)
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = self.write_records(Path(temporary_directory))
            environment = {
                "FORGE_BENCHMARK_RECORDS_PATH": str(path),
                "FORGE_BENCHMARK_SOURCE_CLASSIFICATION": "synthetic",
            }
            with patch.dict(os.environ, environment, clear=False):
                response = client.get("/benchmarks/latest")
                filtered = client.get(
                    "/benchmarks/latest?category=backend&split=holdout"
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["source"]["classification"], "synthetic")
        self.assertEqual(payload["summary"]["counts"]["records"], 2)
        self.assertEqual(payload["summary"]["counts"]["solved"], 1)
        self.assertRegex(payload["full_summary_digest"], r"^[0-9a-f]{64}$")

        self.assertEqual(filtered.status_code, 200)
        selected = filtered.json()
        self.assertEqual(selected["filters"], {"category": "backend", "split": "holdout"})
        self.assertEqual(selected["summary"]["counts"]["records"], 1)
        self.assertEqual(selected["records"][0]["task_id"], "backend-fix-001")

    def test_dashboard_has_explicit_unavailable_and_empty_states(self) -> None:
        client = TestClient(api_main.app)
        with patch.dict(os.environ, {}, clear=True):
            unavailable = client.get("/benchmarks/latest")
        self.assertEqual(unavailable.status_code, 503)
        self.assertEqual(unavailable.json()["detail"], "benchmark evidence is not configured")

        with tempfile.TemporaryDirectory() as temporary_directory:
            path = self.write_records(Path(temporary_directory))
            with patch.dict(
                os.environ,
                {
                    "FORGE_BENCHMARK_RECORDS_PATH": str(path),
                    "FORGE_BENCHMARK_SOURCE_CLASSIFICATION": "development",
                },
                clear=False,
            ):
                empty = client.get("/benchmarks/latest?category=data")
        self.assertEqual(empty.status_code, 200)
        self.assertIsNone(empty.json()["summary"])
        self.assertEqual(empty.json()["records"], [])

    def test_dashboard_rejects_invalid_evidence_and_classification(self) -> None:
        client = TestClient(api_main.app)
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            invalid = directory / "invalid.jsonl"
            invalid.write_text("{not-json}\n", encoding="utf-8")
            with patch.dict(
                os.environ,
                {"FORGE_BENCHMARK_RECORDS_PATH": str(invalid)},
                clear=False,
            ):
                response = client.get("/benchmarks/latest")
            self.assertEqual(response.status_code, 503)
            self.assertIn("benchmark evidence is invalid", response.json()["detail"])

            valid = self.write_records(directory)
            with patch.dict(
                os.environ,
                {
                    "FORGE_BENCHMARK_RECORDS_PATH": str(valid),
                    "FORGE_BENCHMARK_SOURCE_CLASSIFICATION": "unreviewed",
                },
                clear=False,
            ):
                response = client.get("/benchmarks/latest")
            self.assertEqual(response.status_code, 503)
            self.assertEqual(
                response.json()["detail"],
                "benchmark source classification is invalid",
            )


if __name__ == "__main__":
    unittest.main()
