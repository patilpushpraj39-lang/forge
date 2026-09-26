from __future__ import annotations

import unittest
from dataclasses import replace

from forge_evaluation import (
    BenchmarkExperiment,
    BenchmarkSplit,
    BenchmarkTask,
    BenchmarkVerdict,
    export_benchmark_record,
)


class EvidenceStore:
    def __init__(self, run_id: str, state: str, events: list[dict[str, object]]):
        self.run = {"run_id": run_id, "state": state}
        self.events = events

    def get_run(self, run_id: str) -> dict[str, object]:
        del run_id
        return self.run

    def list_events(
        self, run_id: str, after_sequence: int = 0
    ) -> list[dict[str, object]]:
        del run_id, after_sequence
        return self.events


class BenchmarkExportTests(unittest.TestCase):
    run_id = "run-benchmark-001"
    manifest_digest = "c" * 64

    def task(self) -> BenchmarkTask:
        return BenchmarkTask(
            benchmark_version="forge-private-v1",
            task_id="backend-fix-001",
            task_version="task-v1",
            task_manifest_digest="d" * 64,
            split=BenchmarkSplit.HOLDOUT,
            category="backend",
            language="python",
        )

    def experiment(self) -> BenchmarkExperiment:
        return BenchmarkExperiment("tool-loop-v1", "a" * 64, "b" * 40)

    def event(
        self,
        sequence: int,
        event_type: str,
        second: int,
        payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return {
            "event_id": f"event-{sequence}",
            "run_id": self.run_id,
            "sequence": sequence,
            "schema_version": 1,
            "event_type": event_type,
            "occurred_at": f"2026-09-27T00:00:{second:02d}+00:00",
            "actor": "worker",
            "payload": payload or {},
        }

    def agent_event(
        self,
        sequence: int = 2,
        second: int = 5,
        stop_reason: str = "needs_approval",
    ) -> dict[str, object]:
        return self.event(
            sequence,
            "agent_stopped",
            second,
            {
                "stop_reason": stop_reason,
                "cost_microusd": 125_000,
                "tool_calls": 7,
                "patch_attempts": 2,
            },
        )

    def evaluation_events(
        self,
        *,
        verdict: str,
        failures: list[str],
        public_status: str = "passed",
    ) -> list[dict[str, object]]:
        return [
            self.event(
                3,
                "evaluation_started",
                6,
                {"manifest_digest": self.manifest_digest},
            ),
            self.event(
                4,
                "evaluation_completed",
                10,
                {
                    "verdict": verdict,
                    "manifest_digest": self.manifest_digest,
                    "failure_codes": failures,
                    "checks": [
                        {
                            "check_id": "public-tests",
                            "kind": "public_test",
                            "status": public_status,
                            "required": True,
                        },
                        {
                            "check_id": "hidden-tests",
                            "kind": "hidden_test",
                            "status": "failed" if failures else "passed",
                            "required": True,
                        },
                    ],
                },
            ),
        ]

    def test_export_uses_durable_evidence_for_a_verified_solution(self) -> None:
        events = [self.event(1, "run_created", 0), self.agent_event()]
        events.extend(self.evaluation_events(verdict="passed", failures=[]))
        events.append(
            self.event(
                5,
                "state_changed",
                11,
                {"to_state": "AWAITING_APPROVAL"},
            )
        )

        record = export_benchmark_record(
            EvidenceStore(self.run_id, "AWAITING_APPROVAL", events),
            self.run_id,
            self.task(),
            self.experiment(),
        )

        self.assertEqual(record.verdict, BenchmarkVerdict.SOLVED)
        self.assertEqual(record.evaluation_manifest_digest, self.manifest_digest)
        self.assertEqual(record.duration_ms, 10_000)
        self.assertEqual(record.cost_microusd, 125_000)
        self.assertEqual(record.tool_calls, 7)
        self.assertEqual(record.patch_attempts, 2)
        self.assertTrue(record.regression_free)

    def test_evaluation_failures_keep_distinct_failure_classes(self) -> None:
        cases = (
            ("hidden_test_invalid", BenchmarkVerdict.TASK_INVALID, True),
            ("manifest_mismatch", BenchmarkVerdict.INFRA_INVALID, True),
            ("forbidden_path", BenchmarkVerdict.POLICY_VIOLATION, True),
            ("check_timed_out", BenchmarkVerdict.BUDGET_EXHAUSTED, False),
            ("check_failed", BenchmarkVerdict.UNSOLVED, False),
        )
        for failure, expected, regression_free in cases:
            with self.subTest(failure=failure):
                events = [self.event(1, "run_created", 0), self.agent_event()]
                events.extend(
                    self.evaluation_events(
                        verdict="failed",
                        failures=[failure, "rubric_failed"],
                        public_status="passed" if regression_free else "failed",
                    )
                )
                events.append(
                    self.event(
                        5,
                        "state_changed",
                        11,
                        {
                            "to_state": "FAILED",
                            "reason": "independent_evaluation_failed",
                        },
                    )
                )

                record = export_benchmark_record(
                    EvidenceStore(self.run_id, "FAILED", events),
                    self.run_id,
                    self.task(),
                    self.experiment(),
                )

                self.assertEqual(record.verdict, expected)
                self.assertEqual(record.failure_code, failure)
                self.assertEqual(record.regression_free, regression_free)

    def test_pre_evaluation_budget_and_infrastructure_failures_are_exported(self) -> None:
        budget_events = [
            self.event(1, "run_created", 0),
            self.agent_event(stop_reason="token_budget_exhausted"),
            self.event(
                3,
                "state_changed",
                6,
                {"to_state": "FAILED", "reason": "token_budget_exhausted"},
            ),
        ]
        budget = export_benchmark_record(
            EvidenceStore(self.run_id, "FAILED", budget_events),
            self.run_id,
            self.task(),
            self.experiment(),
        )
        self.assertEqual(budget.verdict, BenchmarkVerdict.BUDGET_EXHAUSTED)
        self.assertIsNone(budget.evaluation_manifest_digest)
        self.assertEqual(budget.duration_ms, 6_000)

        infrastructure_events = [
            self.event(1, "run_created", 0),
            self.event(
                2,
                "state_changed",
                2,
                {"to_state": "FAILED", "reason": "RuntimeError"},
            ),
        ]
        infrastructure = export_benchmark_record(
            EvidenceStore(self.run_id, "FAILED", infrastructure_events),
            self.run_id,
            self.task(),
            self.experiment(),
        )
        self.assertEqual(infrastructure.verdict, BenchmarkVerdict.INFRA_INVALID)
        self.assertEqual(infrastructure.failure_code, "runtimeerror")
        self.assertEqual(infrastructure.cost_microusd, 0)

    def test_export_rejects_incomplete_or_tampered_evidence(self) -> None:
        solved_events = [self.event(1, "run_created", 0), self.agent_event()]
        solved_events.extend(self.evaluation_events(verdict="passed", failures=[]))

        with self.assertRaisesRegex(ValueError, "only after a verdict"):
            export_benchmark_record(
                EvidenceStore(self.run_id, "EXECUTING", solved_events),
                self.run_id,
                self.task(),
                self.experiment(),
            )

        reversed_events = list(reversed(solved_events))
        with self.assertRaisesRegex(ValueError, "unique and increasing"):
            export_benchmark_record(
                EvidenceStore(self.run_id, "AWAITING_APPROVAL", reversed_events),
                self.run_id,
                self.task(),
                self.experiment(),
            )

        mismatched = list(solved_events)
        mismatched[3] = {
            **mismatched[3],
            "payload": {
                **mismatched[3]["payload"],
                "manifest_digest": "e" * 64,
            },
        }
        with self.assertRaisesRegex(ValueError, "disagree"):
            export_benchmark_record(
                EvidenceStore(self.run_id, "AWAITING_APPROVAL", mismatched),
                self.run_id,
                self.task(),
                self.experiment(),
            )

        missing_metric = list(solved_events)
        missing_metric[1] = {
            **missing_metric[1],
            "payload": {
                key: value
                for key, value in missing_metric[1]["payload"].items()
                if key != "patch_attempts"
            },
        }
        with self.assertRaisesRegex(ValueError, "patch_attempts"):
            export_benchmark_record(
                EvidenceStore(self.run_id, "AWAITING_APPROVAL", missing_metric),
                self.run_id,
                self.task(),
                self.experiment(),
            )

        wrong_task = replace(self.task(), task_manifest_digest="not-a-digest")
        with self.assertRaisesRegex(ValueError, "task_manifest_digest"):
            export_benchmark_record(
                EvidenceStore(self.run_id, "AWAITING_APPROVAL", solved_events),
                self.run_id,
                wrong_task,
                self.experiment(),
            )


if __name__ == "__main__":
    unittest.main()
