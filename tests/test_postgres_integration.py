from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = os.environ.get("FORGE_TEST_DATABASE_URL")


@unittest.skipUnless(DATABASE_URL, "FORGE_TEST_DATABASE_URL is not configured")
class PostgresIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import psycopg

        from forge_agent_core.postgres_run_store import PostgresRunStore

        assert DATABASE_URL is not None
        cls.database_url = DATABASE_URL
        database_name = urlparse(cls.database_url).path.lstrip("/")
        if "test" not in database_name.lower():
            raise RuntimeError(
                "FORGE_TEST_DATABASE_URL must identify a dedicated test database"
            )
        cls.migrations_path = ROOT / "db" / "migrations"
        cls.store = PostgresRunStore(cls.database_url, cls.migrations_path)
        with psycopg.connect(cls.database_url) as connection:
            connection.execute(
                "truncate table agent_idempotency_records, outbox_messages, "
                "run_events, runs "
                "restart identity cascade"
            )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.store.close()

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.repository = Path(self.temporary_directory.name) / "repository"
        self.repository.mkdir()
        (self.repository / "README.md").write_text(
            "postgres integration fixture\n", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_migrations_are_idempotent_and_checksum_protected(self) -> None:
        from forge_agent_core.postgres_run_store import (
            MigrationChecksumError,
            PostgresRunStore,
            apply_migrations,
        )

        reopened_store = PostgresRunStore(self.database_url, self.migrations_path)
        reopened_store.close()
        with tempfile.TemporaryDirectory() as temporary_directory:
            copied_migrations = Path(temporary_directory) / "migrations"
            shutil.copytree(self.migrations_path, copied_migrations)
            apply_migrations(self.database_url, copied_migrations)
            migration = copied_migrations / "0001_walking_skeleton.sql"
            migration.write_text(
                migration.read_text(encoding="utf-8") + "\n-- changed\n",
                encoding="utf-8",
            )
            with self.assertRaises(MigrationChecksumError):
                apply_migrations(self.database_url, copied_migrations)

    def test_parallel_workers_claim_distinct_runs(self) -> None:
        created = [
            self.store.create_run(str(self.repository)),
            self.store.create_run(str(self.repository)),
        ]
        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = list(
                executor.map(
                    lambda worker_id: self.store.claim_next_run(worker_id),
                    ("postgres-worker-one", "postgres-worker-two"),
                )
            )

        self.assertNotIn(None, claims)
        claimed_ids = {str(claim["run_id"]) for claim in claims if claim}
        self.assertEqual(claimed_ids, {str(run["run_id"]) for run in created})

    def test_agent_idempotency_survives_ledger_recreation(self) -> None:
        from forge_agent_core.idempotency import PostgresIdempotencyLedger
        from forge_agent_core.model_runtime import (
            ModelStepResult,
            StopReason,
            TokenUsage,
            ToolCall,
        )

        expected = ModelStepResult(
            provider="fixture",
            model="fixture-model",
            response_id="response-1",
            stop_reason=StopReason.TOOL_REQUESTED,
            usage=TokenUsage(10, 2, 3, 1),
            cost_microusd=12,
            prompt_version="prompt-v1",
            tool_version="tools-v1",
            tool_calls=(
                ToolCall("call-1", "read_file", {"path": "app.py"}),
            ),
        )
        first = PostgresIdempotencyLedger(
            self.database_url, self.migrations_path
        )
        try:
            first.put_model_step("a" * 64, expected)
        finally:
            first.close()

        second = PostgresIdempotencyLedger(
            self.database_url, self.migrations_path
        )
        try:
            recovered = second.get_model_step("a" * 64)
        finally:
            second.close()

        self.assertEqual(recovered, expected)

    def test_postgres_worker_writes_events_and_transactional_outbox(self) -> None:
        import psycopg

        from forge_worker.main import run_once

        created = self.store.create_run(str(self.repository))
        self.assertTrue(run_once(self.store, worker_id="postgres-worker"))

        completed = self.store.get_run(str(created["run_id"]))
        events = self.store.list_events(str(created["run_id"]))
        self.assertEqual(completed["state"], "COMPLETED")
        self.assertGreater(int(completed["row_version"]), 0)
        self.assertEqual([event["sequence"] for event in events], list(range(1, 10)))

        with psycopg.connect(self.database_url) as connection:
            outbox_count = connection.execute(
                """
                select count(*)
                from outbox_messages
                where aggregate_id = %s
                """,
                (str(created["run_id"]),),
            ).fetchone()[0]
        self.assertEqual(outbox_count, 9)

    def test_live_api_worker_and_sse_replay(self) -> None:
        from forge_agent_core.postgres_run_store import PostgresRunStore
        from forge_worker.main import run_once

        port = self._available_port()
        environment = os.environ.copy()
        environment["FORGE_DATABASE_URL"] = self.database_url
        environment["FORGE_MIGRATIONS_PATH"] = str(self.migrations_path)
        source_paths = [
            ROOT / "packages" / "agent-core" / "src",
            ROOT / "packages" / "repo-intelligence" / "src",
            ROOT / "services" / "api" / "src",
            ROOT / "services" / "sandbox-controller" / "src",
            ROOT / "services" / "worker" / "src",
        ]
        environment["PYTHONPATH"] = os.pathsep.join(
            [str(path) for path in source_paths]
            + ([environment["PYTHONPATH"]] if environment.get("PYTHONPATH") else [])
        )
        api = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "forge_api.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=ROOT,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            self._wait_for_health(port, api)
            created = self._request_json(
                f"http://127.0.0.1:{port}/runs",
                method="POST",
                payload={
                    "repository_path": str(self.repository),
                    "objective": "Inspect this fixture and verify it.",
                    "budgets": {
                        "total_tokens": 10_000,
                        "cost_microusd": 250_000,
                        "wall_seconds": 120,
                        "model_steps": 10,
                        "tool_calls": 20,
                        "patch_attempts": 2,
                    },
                },
            )
            store = PostgresRunStore(self.database_url, self.migrations_path)
            try:
                self.assertTrue(run_once(store, worker_id="postgres-live-test"))
            finally:
                store.close()

            final_run = self._request_json(
                f"http://127.0.0.1:{port}/runs/{created['run_id']}"
            )
            self.assertEqual(final_run["state"], "COMPLETED")
            self.assertEqual(
                final_run["objective"],
                "Inspect this fixture and verify it.",
            )
            self.assertEqual(final_run["budget_total_tokens"], 10_000)
            events = self._read_sse_events(
                f"http://127.0.0.1:{port}/runs/{created['run_id']}/events/stream"
            )
            self.assertEqual([event["sequence"] for event in events], list(range(1, 10)))
            self.assertEqual(events[-1]["event_type"], "workspace_destroyed")
        finally:
            api.terminate()
            try:
                api.wait(timeout=5)
            except subprocess.TimeoutExpired:
                api.kill()
                api.wait(timeout=5)

    @staticmethod
    def _available_port() -> int:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            return int(listener.getsockname()[1])

    @staticmethod
    def _wait_for_health(port: int, process: subprocess.Popen[bytes]) -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Forge API exited before becoming healthy")
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/health", timeout=1
                ) as response:
                    if response.status == 200:
                        return
            except OSError:
                time.sleep(0.1)
        raise TimeoutError("Forge API did not become healthy")

    @staticmethod
    def _request_json(
        url: str,
        *,
        method: str = "GET",
        payload: dict[str, str] | None = None,
    ) -> dict[str, object]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={"content-type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())

    @staticmethod
    def _read_sse_events(url: str) -> list[dict[str, object]]:
        events: list[dict[str, object]] = []
        with urllib.request.urlopen(url, timeout=10) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8").strip()
                if not line.startswith("data: "):
                    continue
                events.append(json.loads(line.removeprefix("data: ")))
                if len(events) == 9:
                    break
        return events


if __name__ == "__main__":
    unittest.main()
