from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
import hashlib
from contextlib import closing
from pathlib import Path

from forge_agent_core import SqliteIdempotencyLedger
from forge_agent_core.model_runtime import (
    BudgetLimits,
    ModelStepResult,
    StopReason,
    TokenUsage,
    ToolCall,
)
from forge_agent_core.run_store import RunState, RunStore
from forge_evaluation import CheckKind, CheckSpec
from forge_sandbox_controller import (
    LocalSandboxController,
    SandboxNotFoundError,
)
from forge_worker.main import run_once
from forge_worker.evaluation_execution import EvaluationTemplate


def agent_step(
    response_id: str,
    *,
    calls: tuple[ToolCall, ...] = (),
    output: dict[str, object] | None = None,
) -> ModelStepResult:
    return ModelStepResult(
        provider="fixture",
        model="fixture-model",
        response_id=response_id,
        stop_reason=(
            StopReason.TOOL_REQUESTED if calls else StopReason.COMPLETED
        ),
        usage=TokenUsage(10, 5),
        cost_microusd=25,
        prompt_version="forge-agent-v1",
        tool_version="forge-tools-v1",
        tool_calls=calls,
        structured_output=output,
    )


class ScriptedAgentRuntime:
    def __init__(self, results: list[ModelStepResult]) -> None:
        self.results = results
        self.requests = []

    def run_step(self, request):
        self.requests.append(request)
        return self.results.pop(0)


class SlowCompletingRuntime:
    def run_step(self, request):
        time.sleep(0.4)
        return agent_step(
            "slow-response",
            output={
                "status": "completed",
                "summary": "No source change was needed.",
                "verification": ["Repository context inspected."],
                "risks": [],
            },
        )


class WalkingSkeletonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database = self.root / "forge.db"
        self.repository = self.root / "repository"
        self.repository.mkdir()
        (self.repository / "README.md").write_text("fixture\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    @staticmethod
    def readme_evaluation(*, should_pass: bool = True) -> EvaluationTemplate:
        expected = "# Fixture\n" if should_pass else "not-the-patch\n"
        return EvaluationTemplate(
            benchmark_version="walking-skeleton-v1",
            task_id="readme-heading",
            task_version="1",
            fixture_version="1",
            sandbox_image_digest=(
                "forge-test-evaluator@sha256:"
                + hashlib.sha256(b"forge-test-evaluator-v1").hexdigest()
            ),
            public_checks=(
                CheckSpec(
                    "readme-verification",
                    CheckKind.PUBLIC_TEST,
                    (
                        "python",
                        "-c",
                        "from pathlib import Path; "
                        f"assert Path('README.md').read_text() == {expected!r}",
                    ),
                ),
            ),
        )

    def test_run_persists_across_store_instances(self) -> None:
        first_store = RunStore(self.database)
        objective = "Fix the README heading and verify the change."
        budgets = BudgetLimits(12_000, 250_000, 90, 8, 20, 2)
        created = first_store.create_run(
            str(self.repository), objective, budgets
        )

        reopened_store = RunStore(self.database)
        loaded = reopened_store.get_run(str(created["run_id"]))
        events = reopened_store.list_events(str(created["run_id"]))

        self.assertEqual(loaded["state"], RunState.CREATED)
        self.assertEqual(loaded["objective"], objective)
        self.assertEqual(loaded["budget_total_tokens"], 12_000)
        self.assertEqual(loaded["budget_cost_microusd"], 250_000)
        self.assertEqual(loaded["budget_wall_seconds"], 90)
        self.assertEqual(loaded["budget_model_steps"], 8)
        self.assertEqual(loaded["budget_tool_calls"], 20)
        self.assertEqual(loaded["budget_patch_attempts"], 2)
        self.assertEqual([event["event_type"] for event in events], ["run_created"])
        self.assertEqual(events[0]["payload"]["objective"], objective)
        self.assertEqual(
            events[0]["payload"]["budgets"],
            {
                "total_tokens": 12_000,
                "cost_microusd": 250_000,
                "wall_seconds": 90,
                "model_steps": 8,
                "tool_calls": 20,
                "patch_attempts": 2,
            },
        )

    def test_existing_sqlite_database_receives_safe_run_defaults(self) -> None:
        import sqlite3

        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                """
                CREATE TABLE runs (
                    run_id TEXT PRIMARY KEY,
                    repository_path TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE run_events (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    sequence INTEGER NOT NULL,
                    schema_version INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE(run_id, sequence)
                )
                """
            )
            connection.execute(
                """
                INSERT INTO runs(
                    run_id, repository_path, state, created_at, updated_at
                ) VALUES ('legacy', 'fixture', 'CREATED', 'now', 'now')
                """
            )
            connection.commit()

        migrated = RunStore(self.database).get_run("legacy")

        self.assertEqual(
            migrated["objective"],
            "Inspect the repository and report verified findings.",
        )
        self.assertEqual(migrated["budget_total_tokens"], 50_000)
        self.assertEqual(migrated["budget_patch_attempts"], 5)

    def test_worker_records_snapshot_command_and_cleanup(self) -> None:
        store = RunStore(self.database)
        created = store.create_run(str(self.repository))

        self.assertTrue(run_once(store))

        completed = store.get_run(str(created["run_id"]))
        events = store.list_events(str(created["run_id"]))
        event_types = [event["event_type"] for event in events]

        self.assertEqual(completed["state"], RunState.COMPLETED)
        self.assertEqual([event["sequence"] for event in events], list(range(1, 10)))
        self.assertIn("snapshot_ready", event_types)
        self.assertIn("repository_indexed", event_types)
        self.assertIn("command_started", event_types)
        self.assertIn("command_completed", event_types)
        self.assertEqual(event_types[-1], "workspace_destroyed")
        snapshot_event = next(
            event
            for event in events
            if event["event_type"] == "snapshot_ready"
        )
        self.assertEqual(
            snapshot_event["payload"]["snapshot_artifact"]["media_type"],
            "application/vnd.forge.snapshot+tar",
        )
        brief = next(
            event["payload"]["brief"]
            for event in events
            if event["event_type"] == "repository_indexed"
        )
        self.assertEqual(brief["languages"], {"Markdown": 1})
        self.assertEqual(brief["indexed_file_count"], 1)
        required_event_keys = {
            "event_id",
            "run_id",
            "sequence",
            "schema_version",
            "event_type",
            "occurred_at",
            "actor",
            "payload",
        }
        for event in events:
            self.assertEqual(set(event), required_event_keys)

    def test_bounded_agent_path_produces_patch_awaiting_approval(self) -> None:
        store = RunStore(self.database)
        created = store.create_run(
            str(self.repository),
            "Replace the README text with a Markdown heading.",
        )
        patch = (
            "diff --git a/README.md b/README.md\n"
            "--- a/README.md\n"
            "+++ b/README.md\n"
            "@@ -1 +1 @@\n"
            "-fixture\n"
            "+# Fixture\n"
        )
        patch_hash = hashlib.sha256(patch.encode("utf-8")).hexdigest()
        runtime = ScriptedAgentRuntime(
            [
                agent_step(
                    "response-1",
                    calls=(
                        ToolCall(
                            "call-read",
                            "read_file",
                            {
                                "path": "README.md",
                                "start_line": 1,
                                "end_line": 1,
                            },
                        ),
                    ),
                ),
                agent_step(
                    "response-2",
                    calls=(
                        ToolCall(
                            "call-patch",
                            "apply_patch",
                            {
                                "patch": patch,
                                "expected_sha256": patch_hash,
                            },
                        ),
                    ),
                ),
                agent_step(
                    "response-3",
                    calls=(ToolCall("call-diff", "get_diff", {}),),
                ),
                agent_step(
                    "response-4",
                    output={
                        "status": "needs_approval",
                        "summary": "README heading updated.",
                        "verification": ["Diff contains one text change."],
                        "risks": [],
                    },
                ),
            ]
        )
        ledger = SqliteIdempotencyLedger(self.database)

        self.assertTrue(
            run_once(
                store,
                worker_id="agent-worker",
                model_runtime=runtime,
                idempotency_ledger=ledger,
                evaluation_template=self.readme_evaluation(),
            )
        )

        run_id = str(created["run_id"])
        final_run = store.get_run(run_id)
        events = store.list_events(run_id)
        event_types = [event["event_type"] for event in events]
        self.assertEqual(final_run["state"], RunState.AWAITING_APPROVAL)
        self.assertIsNone(final_run["lease_owner"])
        self.assertIn("agent_started", event_types)
        self.assertIn("agent_stopped", event_types)
        self.assertIn("patch_ready", event_types)
        self.assertIn("evaluation_started", event_types)
        self.assertIn("evaluation_completed", event_types)
        evaluation = next(
            event
            for event in events
            if event["event_type"] == "evaluation_completed"
        )
        self.assertEqual(evaluation["payload"]["verdict"], "passed")
        self.assertEqual(len(evaluation["payload"]["verdict_hash"]), 64)
        self.assertEqual(
            evaluation["payload"]["report_artifact"]["media_type"],
            "application/vnd.forge.evaluation+json",
        )
        self.assertEqual(
            evaluation["payload"]["manifest_artifact"]["media_type"],
            "application/vnd.forge.evaluation-manifest+json",
        )
        self.assertEqual(event_types[-1], "workspace_destroyed")
        patch_event = next(
            event for event in events if event["event_type"] == "patch_ready"
        )
        self.assertEqual(patch_event["payload"]["changed_paths"], ["README.md"])
        self.assertEqual(len(runtime.requests), 4)
        self.assertEqual(runtime.requests[0].objective, final_run["objective"])
        self.assertEqual(runtime.requests[0].budget.total_tokens, 50_000)
        self.assertEqual(
            (self.repository / "README.md").read_text(encoding="utf-8"),
            "fixture\n",
        )

        cancelled = store.cancel_run(run_id)
        self.assertEqual(cancelled["state"], RunState.CANCELLED)

    def test_failed_independent_evaluation_never_reaches_approval(self) -> None:
        store = RunStore(self.database)
        created = store.create_run(
            str(self.repository),
            "Replace the README text with a Markdown heading.",
        )
        patch = (
            "diff --git a/README.md b/README.md\n"
            "--- a/README.md\n"
            "+++ b/README.md\n"
            "@@ -1 +1 @@\n"
            "-fixture\n"
            "+# Fixture\n"
        )
        patch_hash = hashlib.sha256(patch.encode("utf-8")).hexdigest()
        runtime = ScriptedAgentRuntime(
            [
                agent_step(
                    "failure-patch",
                    calls=(
                        ToolCall(
                            "failure-patch-call",
                            "apply_patch",
                            {"patch": patch, "expected_sha256": patch_hash},
                        ),
                    ),
                ),
                agent_step(
                    "failure-complete",
                    output={
                        "status": "needs_approval",
                        "summary": "README heading updated.",
                        "verification": [],
                        "risks": [],
                    },
                ),
            ]
        )

        self.assertTrue(
            run_once(
                store,
                worker_id="failed-evaluation-worker",
                model_runtime=runtime,
                idempotency_ledger=SqliteIdempotencyLedger(self.database),
                evaluation_template=self.readme_evaluation(should_pass=False),
            )
        )

        run_id = str(created["run_id"])
        final_run = store.get_run(run_id)
        events = store.list_events(run_id)
        self.assertEqual(final_run["state"], RunState.FAILED)
        evaluation = next(
            event
            for event in events
            if event["event_type"] == "evaluation_completed"
        )
        self.assertEqual(evaluation["payload"]["verdict"], "failed")
        self.assertIn("check_failed", evaluation["payload"]["failure_codes"])
        self.assertNotIn(
            RunState.AWAITING_APPROVAL,
            [
                event["payload"].get("to_state")
                for event in events
                if event["event_type"] == "state_changed"
            ],
        )

    def test_agent_model_call_heartbeat_prevents_duplicate_claim(self) -> None:
        store = RunStore(self.database)
        created = store.create_run(
            str(self.repository), "Inspect the README without changing it."
        )
        run_id = str(created["run_id"])
        worker = threading.Thread(
            target=run_once,
            kwargs={
                "store": store,
                "worker_id": "slow-agent-worker",
                "lease_seconds": 0.12,
                "model_runtime": SlowCompletingRuntime(),
                "idempotency_ledger": SqliteIdempotencyLedger(self.database),
            },
            daemon=True,
        )
        worker.start()

        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if any(
                event["event_type"] == "agent_started"
                for event in store.list_events(run_id)
            ):
                break
            time.sleep(0.01)
        else:
            self.fail("agent did not start")

        time.sleep(0.22)
        self.assertIsNone(
            store.claim_next_run("duplicate-agent-worker", lease_seconds=1)
        )
        worker.join(timeout=2)

        self.assertFalse(worker.is_alive())
        self.assertEqual(store.get_run(run_id)["state"], RunState.COMPLETED)

    def test_agent_blocked_output_becomes_an_explicit_failure(self) -> None:
        store = RunStore(self.database)
        created = store.create_run(
            str(self.repository), "Perform a task that requires missing input."
        )
        runtime = ScriptedAgentRuntime(
            [
                agent_step(
                    "blocked-response",
                    output={
                        "status": "blocked",
                        "summary": "Required reproduction data is unavailable.",
                        "verification": [],
                        "risks": ["No patch was produced."],
                    },
                )
            ]
        )

        self.assertTrue(
            run_once(
                store,
                worker_id="blocked-agent-worker",
                model_runtime=runtime,
                idempotency_ledger=SqliteIdempotencyLedger(self.database),
            )
        )

        run_id = str(created["run_id"])
        self.assertEqual(store.get_run(run_id)["state"], RunState.FAILED)
        state_event = [
            event
            for event in store.list_events(run_id)
            if event["event_type"] == "state_changed"
        ][-1]
        self.assertEqual(state_event["payload"]["reason"], "agent_blocked")

    def test_cancelled_run_is_not_claimed(self) -> None:
        store = RunStore(self.database)
        created = store.create_run(str(self.repository))
        cancelled = store.cancel_run(str(created["run_id"]))

        self.assertEqual(cancelled["state"], RunState.CANCELLED)
        self.assertFalse(run_once(store))

    def test_expired_lease_is_recovered_by_another_worker(self) -> None:
        store = RunStore(self.database)
        created = store.create_run(str(self.repository))
        first_claim = store.claim_next_run("worker-one", lease_seconds=0.05)
        self.assertIsNotNone(first_claim)

        time.sleep(0.08)
        recovered = store.claim_next_run("worker-two", lease_seconds=1)

        self.assertIsNotNone(recovered)
        assert recovered is not None
        self.assertEqual(recovered["run_id"], created["run_id"])
        self.assertEqual(recovered["state"], RunState.SNAPSHOTTING)
        self.assertEqual(recovered["attempt"], 2)
        self.assertEqual(recovered["lease_owner"], "worker-two")
        events = store.list_events(str(created["run_id"]))
        self.assertEqual(events[-1]["event_type"], "lease_recovered")
        self.assertEqual(events[-1]["payload"]["previous_worker_id"], "worker-one")

    def test_expired_evaluation_lease_is_recovered(self) -> None:
        store = RunStore(self.database)
        created = store.create_run(str(self.repository))
        run_id = str(created["run_id"])
        claimed = store.claim_next_run("evaluator-one", lease_seconds=0.05)
        self.assertIsNotNone(claimed)
        store.transition(
            run_id,
            RunState.SNAPSHOTTING,
            RunState.EXECUTING,
            "worker",
            lease_owner="evaluator-one",
        )
        store.transition(
            run_id,
            RunState.EXECUTING,
            RunState.EVALUATING,
            "evaluator",
            lease_owner="evaluator-one",
        )

        time.sleep(0.08)
        recovered = store.claim_next_run("evaluator-two", lease_seconds=1)

        self.assertIsNotNone(recovered)
        assert recovered is not None
        self.assertEqual(recovered["state"], RunState.SNAPSHOTTING)
        self.assertEqual(recovered["attempt"], 2)
        recovery = store.list_events(run_id)[-1]
        self.assertEqual(recovery["event_type"], "lease_recovered")
        self.assertEqual(recovery["payload"]["from_state"], RunState.EVALUATING)

    def test_active_command_heartbeats_prevent_duplicate_claim(self) -> None:
        store = RunStore(self.database)
        created = store.create_run(str(self.repository))
        run_id = str(created["run_id"])
        worker = threading.Thread(
            target=run_once,
            kwargs={
                "store": store,
                "worker_id": "worker-heartbeat",
                "command": (sys.executable, "-c", "import time; time.sleep(0.5)"),
                "timeout_seconds": 2,
                "lease_seconds": 0.15,
            },
            daemon=True,
        )
        worker.start()

        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            event_types = [
                event["event_type"] for event in store.list_events(run_id)
            ]
            if "command_started" in event_types:
                break
            time.sleep(0.01)
        else:
            self.fail("worker did not start the command")

        time.sleep(0.22)
        self.assertIsNone(store.claim_next_run("duplicate-worker", lease_seconds=1))
        worker.join(timeout=3)

        self.assertFalse(worker.is_alive())
        completed = store.get_run(run_id)
        self.assertEqual(completed["state"], RunState.COMPLETED)
        self.assertEqual(completed["attempt"], 1)

    def test_active_command_is_terminated_after_cancellation(self) -> None:
        store = RunStore(self.database)
        created = store.create_run(str(self.repository))
        run_id = str(created["run_id"])
        long_command = (sys.executable, "-c", "import time; time.sleep(10)")
        worker = threading.Thread(
            target=run_once,
            kwargs={
                "store": store,
                "worker_id": "worker-cancel",
                "command": long_command,
                "timeout_seconds": 5,
                "lease_seconds": 0.3,
            },
            daemon=True,
        )
        worker.start()

        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            event_types = [
                event["event_type"] for event in store.list_events(run_id)
            ]
            if "command_started" in event_types:
                break
            time.sleep(0.01)
        else:
            self.fail("worker did not start the command")

        cancellation_started = time.monotonic()
        store.request_cancellation(run_id)
        worker.join(timeout=3)

        self.assertFalse(worker.is_alive())
        self.assertLess(time.monotonic() - cancellation_started, 2.5)
        self.assertEqual(store.get_run(run_id)["state"], RunState.CANCELLED)
        event_types = [event["event_type"] for event in store.list_events(run_id)]
        self.assertIn("cancellation_requested", event_types)
        self.assertIn("command_cancelled", event_types)
        self.assertEqual(event_types[-1], "workspace_destroyed")

    def test_command_timeout_fails_run_and_releases_lease(self) -> None:
        store = RunStore(self.database)
        created = store.create_run(str(self.repository))
        slow_command = (sys.executable, "-c", "import time; time.sleep(2)")

        self.assertTrue(
            run_once(
                store,
                worker_id="worker-timeout",
                command=slow_command,
                timeout_seconds=0.1,
                lease_seconds=1,
            )
        )

        run = store.get_run(str(created["run_id"]))
        self.assertEqual(run["state"], RunState.FAILED)
        self.assertIsNone(run["lease_owner"])
        events = store.list_events(str(created["run_id"]))
        self.assertIn("command_timed_out", [event["event_type"] for event in events])

    def test_local_sandbox_uses_opaque_handle_and_is_destroyed(self) -> None:
        controller = LocalSandboxController()
        sandbox = controller.create(self.repository)

        self.assertNotIn(str(self.root), sandbox.sandbox_id)
        result = controller.execute(
            sandbox.sandbox_id,
            (sys.executable, "-c", "print('sandbox-ok')"),
            timeout_seconds=1,
            should_cancel=lambda: False,
            heartbeat=lambda: None,
            heartbeat_interval_seconds=0.1,
        )
        controller.destroy(sandbox.sandbox_id)

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.output.strip(), "sandbox-ok")
        self.assertEqual(
            (self.repository / "README.md").read_text(encoding="utf-8"),
            "fixture\n",
        )
        with self.assertRaises(SandboxNotFoundError):
            controller.destroy(sandbox.sandbox_id)


if __name__ == "__main__":
    unittest.main()
