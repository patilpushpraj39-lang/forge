from __future__ import annotations

import copy
import importlib.util
import io
import json
import os
import socket
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    "readonly_pilot_executor", Path(__file__).resolve().parents[1] / "scripts/execute-readonly-pilot.py",
)
assert SPEC and SPEC.loader
executor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(executor)


class FakeHTTP:
    """Exercises the real adapter/engine with no sockets, key or real provider."""

    def __init__(self):
        self.requests = []
        self.hosts = []
        self.closed = 0
        self.count = {"object": "response.input_tokens", "input_tokens": 128}
        self.generated = {
            "object": "response", "model": "gpt-6-sol", "status": "completed", "service_tier": "default",
            "store": False, "tools": [], "error": None, "background": False, "max_output_tokens": 512,
            "usage": {"input_tokens": 128, "output_tokens": 48, "total_tokens": 176},
            "output": [{"type": "message", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": "FAKE summary for testing only.", "annotations": []}]}],
        }
        self.fail_path = None
        self.status = 200
        self.content_type = "application/json; charset=utf-8"
        self.raw = None
        self.on_count = None
        self.on_generate = None

    def connection(self, host, *, timeout):
        self.hosts.append((host, timeout))
        owner = self

        class Connection:
            def request(self, method, path, *, body, headers):
                self.path = path
                owner.requests.append((method, path, json.loads(body), dict(headers)))
                callback = owner.on_count if path.endswith("input_tokens") else owner.on_generate
                if callback:
                    callback()
                if path == owner.fail_path:
                    raise TimeoutError("secret-key/private-readme/provider-error")

            def getresponse(self):
                value = owner.count if self.path.endswith("input_tokens") else owner.generated
                response = mock.Mock(status=owner.status)
                response.getheader.return_value = owner.content_type
                response.read.return_value = json.dumps(value).encode() if owner.raw is None else owner.raw
                return response

            def close(self):
                owner.closed += 1

        return Connection()


class ReadonlyPilotExecutorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.root_patch = mock.patch.object(executor, "ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.now = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
        clock_patch = mock.patch.object(executor, "utc_now", return_value=self.now)
        self.clock = clock_patch.start()
        self.addCleanup(clock_patch.stop)
        fixture = executor.guards.fixture_plan(self.root)
        (self.root / ".state").mkdir()
        (self.root / "fixture-artifacts").rename(self.root / ".state/artifacts")
        artifact = next((self.root / ".state/artifacts").glob("sha256/*/*"))
        self.run = {
            "run_id": fixture["source_run_id"], "state": "COMPLETED", "objective": executor.preview.SOURCE_OBJECTIVE,
            "repository_owner": "offline", "repository_name": "fixture", "base_sha": fixture["base_sha"],
            "repository_path": "github://offline/fixture@" + fixture["base_sha"],
            "source_snapshot_sha256": fixture["snapshot_sha256"], "source_snapshot_size_bytes": artifact.stat().st_size,
            "source_snapshot_media_type": "application/vnd.forge.snapshot+tar",
        }
        self.database = self.root / ".state/forge.db"
        with closing(sqlite3.connect(self.database)) as connection, connection:
            columns = ", ".join('"' + key + '"' for key in self.run)
            connection.execute(f"CREATE TABLE runs ({columns})")
            placeholders = ", ".join("?" for _ in self.run)
            connection.execute(f"INSERT INTO runs VALUES ({placeholders})", tuple(self.run.values()))
        self.plan = executor.preview.prepare_plan(self.run, self.root / ".state/artifacts", "offline/fixture")
        self.project = "proj_fake_fixture"
        self.consent = {
            "scope": executor.CONSENT_SCOPE, "plan_sha256": self.plan["plan_sha256"],
            "source_run_id": self.plan["source_run_id"], "repository": self.plan["repository"],
            "base_sha": self.plan["base_sha"], "project_id": self.project,
            "allowance_microusd": 20000, "price_reviewed_date": "2026-10-03",
            "allow_source_upload": True, "allow_one_paid_generation": True, "billing_and_model_verified": True,
            "acknowledge_retention": True, "acknowledge_conditional_estimate": True,
            "confirmed_at": self.now.isoformat(), "expires_at": (self.now + timedelta(minutes=10)).isoformat(),
        }
        self.fake = FakeHTTP()
        network_patch = mock.patch.object(executor.http.client, "HTTPSConnection", side_effect=self.fake.connection)
        self.connection = network_patch.start()
        self.addCleanup(network_patch.stop)
        socket_patch = mock.patch.object(socket, "socket", side_effect=AssertionError("No real network"))
        socket_patch.start()
        self.addCleanup(socket_patch.stop)
        self.ledger_path = self.root / ".state/readonly-ai-pilot.sqlite3"

    def execute(self, **changes):
        kwargs = {"enabled": True, "api_key": "fake_fixture_not_a_key", "project_id": self.project}
        plan = changes.pop("plan", self.plan)
        consent = changes.pop("consent", self.consent)
        return executor.execute(plan, consent, **{**kwargs, **changes})

    def state(self):
        with closing(sqlite3.connect(self.ledger_path.as_uri() + "?mode=ro", uri=True)) as connection:
            return connection.execute("SELECT state FROM readonly_ai_attempt WHERE slot = 1").fetchone()[0]

    def test_disabled_default_precedes_every_source_ledger_and_credential_operation(self):
        with mock.patch.object(executor, "verify_saved_source") as source, \
                mock.patch.object(executor, "PilotJournal") as journal, \
                mock.patch.object(os, "getenv", side_effect=AssertionError):
            for enabled in (False, None, 1, "true"):
                with self.subTest(enabled=enabled), self.assertRaisesRegex(executor.PilotStopped, "disabled"):
                    self.execute(enabled=enabled, plan=None, consent=None)
            with self.assertRaisesRegex(executor.PilotStopped, "disabled"):
                executor.execute()
        source.assert_not_called()
        journal.assert_not_called()
        self.connection.assert_not_called()
        self.assertFalse(self.ledger_path.exists())

    def test_real_adapter_path_uses_fixed_host_identical_input_and_safe_request(self):
        before = self.database.read_bytes()
        result = self.execute()
        self.assertEqual(result["state"], "COMPLETED")
        self.assertFalse(result["billing_verified"])
        self.assertTrue(result["summary_untrusted"])
        self.assertEqual(result["conditional_max_estimate_microusd"], 5984)
        self.assertEqual(result["usage_estimate_microusd"], 880)
        self.assertEqual(self.state(), "COMPLETED")
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(self.fake.hosts, [("api.openai.com", 30), ("api.openai.com", 30)])
        self.assertEqual(self.fake.closed, 2)
        counted, generated = self.fake.requests
        self.assertEqual(counted[1], "/v1/responses/input_tokens")
        self.assertEqual(generated[1], "/v1/responses")
        for key in ("model", "instructions", "input", "tools", "reasoning"):
            self.assertEqual(counted[2][key], generated[2][key])
        self.assertEqual(generated[2], self.plan["request"])
        self.assertEqual(set(counted[2]), {"model", "instructions", "input", "tools", "reasoning"})
        self.assertEqual(generated[3]["OpenAI-Project"], self.project)

    def test_wrong_consent_project_flags_expiry_and_price_never_upload(self):
        variants = [{"scope": "other"}, {"plan_sha256": "b" * 64}, {"source_run_id": "other"},
                    {"repository": "other/repo"}, {"base_sha": "b" * 40}, {"project_id": "proj_other"},
                    {"price_reviewed_date": "2026-10-02"}, {"allowance_microusd": True},
                    {"allowance_microusd": 20001}, {"confirmed_at": (self.now + timedelta(seconds=1)).isoformat()},
                    {"expires_at": self.now.isoformat()}, {"confirmed_at": self.now.replace(tzinfo=None).isoformat()},
                    {"expires_at": (self.now + timedelta(minutes=16)).isoformat()}, {"extra": "not permitted"}]
        variants += [{field: 1} for field in ("allow_source_upload", "allow_one_paid_generation",
                    "billing_and_model_verified", "acknowledge_retention", "acknowledge_conditional_estimate")]
        for changes in variants:
            with self.subTest(changes=changes), self.assertRaises(executor.PilotStopped):
                self.execute(consent={**self.consent, **changes})
        self.connection.assert_not_called()
        self.assertFalse(self.ledger_path.exists())

    def test_credentials_are_explicit_and_header_injection_rejected(self):
        for changes in ({"api_key": None}, {"api_key": "fake\r\nAuthorization: x"},
                        {"api_key": "short"}, {"project_id": None}, {"project_id": "proj_x\n"}):
            with self.subTest(changes=changes), self.assertRaises(executor.PilotStopped):
                self.execute(**changes)
        self.connection.assert_not_called()
        self.assertFalse(self.ledger_path.exists())

    def test_request_and_limit_overrides_rejected_even_with_new_hash(self):
        for key, value in (("tools", [{"type": "web_search"}]), ("store", True), ("max_output_tokens", 513),
                           ("model", "other"), ("service_tier", "priority"), ("stream", True), ("previous_response_id", "x")):
            plan = copy.deepcopy(self.plan)
            plan["request"][key] = value
            plan["plan_sha256"] = executor.preview.plan_digest(plan)
            with self.subTest(key=key), self.assertRaises(executor.PilotStopped):
                self.execute(plan=plan, consent={**self.consent, "plan_sha256": plan["plan_sha256"]})
        self.connection.assert_not_called()

    def test_source_reverified_readonly_and_changed_database_or_archive_blocks_upload(self):
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("UPDATE runs SET state = 'CANCELLED'")
        before = self.database.read_bytes()
        with self.assertRaisesRegex(executor.PilotStopped, "immutable"):
            self.execute()
        self.assertEqual(self.database.read_bytes(), before)
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("UPDATE runs SET state = 'COMPLETED'")
        artifact = next((self.root / ".state/artifacts").glob("sha256/*/*"))
        artifact.write_bytes(b"corrupt")
        with self.assertRaises(executor.PilotStopped):
            self.execute()
        self.connection.assert_not_called()

    def test_ledger_in_wrong_existing_database_is_not_modified(self):
        self.ledger_path.write_bytes(self.database.read_bytes())
        before = self.ledger_path.read_bytes()
        with self.assertRaises(executor.PilotStopped):
            self.execute()
        self.assertEqual(self.ledger_path.read_bytes(), before)
        self.connection.assert_not_called()

    def test_linked_ledger_is_rejected_before_opening_it(self):
        original = Path.is_symlink
        with mock.patch.object(Path, "is_symlink", lambda path: path == self.ledger_path or original(path)), \
                self.assertRaises(executor.PilotStopped):
            self.execute()
        self.connection.assert_not_called()
        self.assertFalse(self.ledger_path.exists())

    def test_reopen_and_new_plan_or_project_still_cannot_replay_single_slot(self):
        self.execute()
        # Even an otherwise valid new consent/project cannot allocate another slot.
        with self.assertRaisesRegex(executor.PilotStopped, "already reserved"):
            self.execute(project_id="proj_new", consent={**self.consent, "project_id": "proj_new"})
        with self.assertRaises(executor.PilotStopped):
            self.execute()
        self.assertEqual(len(self.fake.requests), 2)

    def test_reserved_before_count_started_before_generation_and_timeout_blocks_replay(self):
        self.fake.on_count = lambda: self.assertEqual(self.state(), "RESERVED")
        self.fake.on_generate = lambda: self.assertEqual(self.state(), "GENERATION_STARTED")
        self.fake.fail_path = "/v1/responses"
        with self.assertRaisesRegex(executor.PilotStopped, "uncertain") as error:
            self.execute()
        self.assertNotIn("secret-key", str(error.exception))
        self.assertEqual(self.state(), "UNKNOWN_OUTCOME")
        with self.assertRaises(executor.PilotStopped):
            self.execute()
        self.assertEqual(len(self.fake.requests), 2)

    def test_count_error_or_over_budget_is_not_retried_and_never_generates(self):
        self.fake.count["input_tokens"] = 2049
        with self.assertRaises(executor.PilotStopped):
            self.execute()
        self.assertEqual(self.state(), "BLOCKED_BEFORE_GENERATION")
        with self.assertRaises(executor.PilotStopped):
            self.execute()
        self.assertEqual(len(self.fake.requests), 1)

    def test_count_transport_timeout_is_sanitized_and_never_retried(self):
        self.fake.fail_path = "/v1/responses/input_tokens"
        with self.assertRaises(executor.PilotStopped) as error:
            self.execute()
        self.assertNotIn("secret-key", str(error.exception))
        self.assertEqual(self.state(), "BLOCKED_BEFORE_GENERATION")
        with self.assertRaises(executor.PilotStopped):
            self.execute()
        self.assertEqual(len(self.fake.requests), 1)

    def test_count_body_mutation_cannot_change_frozen_generation(self):
        def mutate():
            self.plan["request"]["max_output_tokens"] = 99999
            self.plan["request"]["tools"] = [{"type": "web_search"}]
            self.consent["allowance_microusd"] = 99999
        self.fake.on_count = mutate
        original = copy.deepcopy(self.plan["request"])
        self.execute()
        self.assertEqual(self.fake.requests[1][2], original)

    def test_invalid_count_shapes_and_small_allowance_fail_closed(self):
        for count in (None, True, 128.0, 0, -1, "128"):
            with self.subTest(count=count), self.assertRaises(ValueError):
                executor.preview.check_input_budget(count)
        self.fake.count["object"] = "unexpected"
        with self.assertRaises(executor.PilotStopped):
            self.execute()
        self.assertEqual(len(self.fake.requests), 1)

    def test_low_confirmed_allowance_prevents_generation(self):
        with self.assertRaises(executor.PilotStopped):
            self.execute(consent={**self.consent, "allowance_microusd": 5000})
        self.assertEqual(len(self.fake.requests), 1)
        self.assertEqual(self.state(), "BLOCKED_BEFORE_GENERATION")

    def test_expiry_during_count_and_midnight_prevent_generation(self):
        self.fake.on_count = lambda: setattr(self.clock, "return_value", self.now + timedelta(minutes=11))
        with self.assertRaises(executor.PilotStopped):
            self.execute()
        self.assertEqual(len(self.fake.requests), 1)
        self.assertEqual(self.state(), "BLOCKED_BEFORE_GENERATION")

    def test_price_day_changes_during_count_and_no_new_review_is_inferred(self):
        self.now = datetime(2026, 10, 3, 23, 59, tzinfo=timezone.utc)
        self.clock.return_value = self.now
        self.consent.update(confirmed_at=self.now.isoformat(), expires_at=(self.now + timedelta(minutes=10)).isoformat())
        self.fake.on_count = lambda: setattr(self.clock, "return_value", self.now + timedelta(minutes=2))
        with self.assertRaises(executor.PilotStopped):
            self.execute()
        self.assertEqual(self.state(), "BLOCKED_BEFORE_GENERATION")
        self.assertEqual(len(self.fake.requests), 1)

    def test_stale_real_day_is_not_replaced_with_simulated_price_day(self):
        self.clock.return_value = self.now + timedelta(days=1)
        with self.assertRaises(executor.PilotStopped):
            self.execute()
        self.connection.assert_not_called()

    def test_interrupt_leaves_committed_start_and_replay_remains_blocked(self):
        self.fake.on_generate = lambda: (_ for _ in ()).throw(KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            self.execute()
        self.assertEqual(self.state(), "GENERATION_STARTED")
        with self.assertRaises(executor.PilotStopped):
            self.execute()
        self.assertEqual(len(self.fake.requests), 2)

    def test_journal_commit_failure_prevents_generation(self):
        transition = executor.PilotJournal.transition
        def failing(journal, before, after, **kwargs):
            if after == "GENERATION_STARTED":
                raise sqlite3.OperationalError("private storage error")
            return transition(journal, before, after, **kwargs)
        with mock.patch.object(executor.PilotJournal, "transition", failing), self.assertRaises(executor.PilotStopped):
            self.execute()
        self.assertEqual(len(self.fake.requests), 1)
        with self.assertRaises(executor.PilotStopped):
            self.execute()

    def test_lost_result_commit_is_unknown_not_a_retriable_success(self):
        transition = executor.PilotJournal.transition
        def failing(journal, before, after, **kwargs):
            if after in {"COMPLETED", "UNKNOWN_OUTCOME"}:
                raise sqlite3.OperationalError("private storage error")
            return transition(journal, before, after, **kwargs)
        with mock.patch.object(executor.PilotJournal, "transition", failing), self.assertRaises(executor.PilotStopped):
            self.execute()
        self.assertEqual(self.state(), "GENERATION_STARTED")
        with self.assertRaises(executor.PilotStopped):
            self.execute()
        self.assertEqual(len(self.fake.requests), 2)

    def test_concurrent_attempts_allow_only_one_count_and_generation(self):
        # Preinitialize to test concurrent reservations rather than initialization.
        executor.PilotJournal()
        def attempt(_):
            try:
                return self.execute()["state"]
            except executor.PilotStopped:
                return "blocked"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, range(2)))
        self.assertCountEqual(results, ["COMPLETED", "blocked"])
        self.assertEqual(len(self.fake.requests), 2)

    def test_malformed_response_tools_usage_tier_and_control_text_are_rejected(self):
        variants = [{"tools": [{"type": "web_search"}]}, {"service_tier": "priority"}, {"store": True},
                    {"model": "other"}, {"max_output_tokens": True}, {"status": "queued"}, {"output": []},
                    {"usage": {"input_tokens": 128, "output_tokens": 513, "total_tokens": 641}},
                    {"usage": {"input_tokens": True, "output_tokens": 48, "total_tokens": 176}},
                    {"output": [{"type": "function_call", "name": "apply_patch"}]}]
        for changes in variants:
            with self.subTest(changes=changes), self.assertRaises((ValueError, KeyError)):
                executor.validate_response({**self.fake.generated, **changes}, 128)
        for text in ("\x1b[31m", "x" * 8193):
            response = copy.deepcopy(self.fake.generated)
            response["output"][0]["content"][0]["text"] = text
            with self.assertRaises(ValueError):
                executor.validate_response(response, 128)

    def test_incomplete_response_records_usage_without_claiming_success_or_retry(self):
        self.fake.generated["status"] = "incomplete"
        self.fake.generated["output"] = []
        self.fake.generated["usage"] = {"input_tokens": 128, "output_tokens": 0, "total_tokens": 128}
        result = self.execute()
        self.assertEqual(result["state"], "INCOMPLETE")
        with self.assertRaises(executor.PilotStopped):
            self.execute()
        self.assertEqual(len(self.fake.requests), 2)

    def test_malformed_generation_is_unknown_and_not_retried(self):
        self.fake.generated["usage"]["input_tokens"] = 129
        with self.assertRaises(executor.PilotStopped):
            self.execute()
        self.assertEqual(self.state(), "UNKNOWN_OUTCOME")
        with self.assertRaises(executor.PilotStopped):
            self.execute()
        self.assertEqual(len(self.fake.requests), 2)

    def test_transport_never_follows_redirects_or_retries_errors(self):
        for status in (302, 401, 429, 500):
            self.fake.status = status
            with self.subTest(status=status), self.assertRaises(executor.PilotStopped):
                executor._OpenAIResponses("fake_fixture_not_a_key", self.project).post("/v1/responses", {})
        self.assertEqual(len(self.fake.requests), 4)
        self.assertEqual(self.fake.closed, 4)

    def test_transport_rejects_html_duplicate_json_nonfinite_and_oversize(self):
        for raw in (b"<html>secret-key</html>", b'{"input_tokens":128,"input_tokens":129}',
                    b'{"input_tokens":NaN}', b"x" * (executor.MAX_RESPONSE_BYTES + 1), b"[]"):
            self.fake.raw = raw
            with self.subTest(raw=raw[:20]), self.assertRaises(executor.PilotStopped) as error:
                executor._OpenAIResponses("fake_fixture_not_a_key", self.project).post("/v1/responses", {})
            self.assertNotIn("secret-key", str(error.exception))
        with self.assertRaises(executor.PilotStopped):
            executor._OpenAIResponses("fake_fixture_not_a_key", self.project).post("https://other.example", {})
        self.assertEqual(len(self.fake.requests), 5)

    def test_environment_never_enables_or_redirects_provider_and_ledger_contains_no_text(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "unused-real-looking-key", "OPENAI_BASE_URL": "https://evil.example",
                                         "HTTPS_PROXY": "https://evil.example", "FORGE_READONLY_AI_ENABLED": "true"}), \
                mock.patch.object(os, "getenv", side_effect=AssertionError("No environment reads")):
            with self.assertRaises(executor.PilotStopped):
                executor.execute(self.plan, self.consent)
            self.execute()
        raw = self.ledger_path.read_bytes()
        for value in (b"fake_fixture_not_a_key", b"Disposable repository", b"FAKE summary", self.project.encode()):
            self.assertNotIn(value, raw)
        self.assertTrue(all(host == "api.openai.com" for host, _ in self.fake.hosts))

    def test_default_cli_and_help_cannot_enable_or_change_storage(self):
        before = self.database.read_bytes()
        with redirect_stdout(io.StringIO()) as output, mock.patch.object(sqlite3, "connect") as connect:
            self.assertEqual(executor.main([]), 0)
            self.assertIn("DISABLED", output.getvalue())
            with mock.patch("sys.stderr", io.StringIO()):
                for argv, code in ((["--help"], 0), (["--enable"], 2), (["--execute"], 2), (["--reset"], 2)):
                    with self.assertRaises(SystemExit) as error:
                        executor.main(argv)
                    self.assertEqual(error.exception.code, code)
            connect.assert_not_called()
        self.connection.assert_not_called()
        self.assertEqual(self.database.read_bytes(), before)
        self.assertFalse(self.ledger_path.exists())


if __name__ == "__main__":
    unittest.main()
