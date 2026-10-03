from __future__ import annotations

import copy
import importlib.util
import io
import os
import socket
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from datetime import date, timedelta
from pathlib import Path
from unittest import mock
from uuid import uuid4

SPEC = importlib.util.spec_from_file_location(
    "readonly_pilot_rehearsal", Path(__file__).resolve().parents[1] / "scripts/rehearse-readonly-pilot.py",
)
assert SPEC and SPEC.loader
rehearsal = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rehearsal)


class ReadonlyPilotRehearsalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.plan = rehearsal.fixture_plan(self.root)
        self.backend = rehearsal.FixtureResponses()
        self.journal_path = self.root / "rehearsal.db"
        self.journal = rehearsal.AttemptJournal(self.journal_path)
        self.pilot_id = str(uuid4())
        self.arguments = {
            "pilot_id": self.pilot_id, "confirmed_digest": self.plan["plan_sha256"],
            "allowance": rehearsal.preview.ALLOWANCE_MICROUSD,
            "today": date.fromisoformat(rehearsal.preview.PRICE_DATE),
        }

    def run_rehearsal(self, plan=None, backend=None, journal=None, **changes):
        return rehearsal.rehearse(
            self.plan if plan is None else plan,
            self.backend if backend is None else backend,
            self.journal if journal is None else journal,
            **{**self.arguments, **changes},
        )

    def resign(self, plan):
        plan["plan_sha256"] = rehearsal.preview.plan_digest(plan)
        return plan["plan_sha256"]

    def test_success_records_simulated_usage_and_identical_input_no_real_cost(self):
        report = self.run_rehearsal()
        self.assertEqual(report["state"], "SIMULATED_COMPLETED")
        self.assertTrue(report["simulated"])
        self.assertEqual(report["actual_provider_calls"], 0)
        self.assertEqual(report["actual_charge_microusd"], 0)
        self.assertEqual(report["conditional_max_estimate_microusd"], 5984)
        self.assertEqual(report["hypothetical_usage_cost_microusd"], 880)
        self.assertEqual(self.backend.count_requests, self.backend.generation_requests)
        self.assertEqual(self.backend.generation_requests[0]["tools"], [])
        self.assertEqual(self.journal.get(self.pilot_id)["state"], "SIMULATED_COMPLETED")

    def test_success_cannot_replay_after_journal_reopen(self):
        self.run_rehearsal()
        reopened = rehearsal.AttemptJournal(self.journal_path)
        with self.assertRaisesRegex(rehearsal.RehearsalStopped, "already reserved"):
            self.run_rehearsal(journal=reopened)
        self.assertEqual(len(self.backend.count_requests), 1)
        self.assertEqual(len(self.backend.generation_requests), 1)

    def test_changed_input_without_new_confirmation_never_calls_backend(self):
        plan = copy.deepcopy(self.plan)
        plan["request"]["input"][0]["content"] += "changed"
        for digest in (self.arguments["confirmed_digest"], self.resign(plan)):
            with self.assertRaises(rehearsal.RehearsalStopped):
                self.run_rehearsal(plan, confirmed_digest=digest)
        self.assertEqual(self.backend.count_requests, [])
        self.assertIsNone(self.journal.get(self.pilot_id))

    def test_wrong_or_missing_confirmation_stops_before_reservation(self):
        for digest in (None, "", "a" * 64):
            with self.subTest(digest=digest), self.assertRaises(rehearsal.RehearsalStopped):
                self.run_rehearsal(confirmed_digest=digest)
        self.assertEqual(self.backend.count_requests, [])
        self.assertIsNone(self.journal.get(self.pilot_id))

    def test_valid_changed_readme_requires_new_confirmation(self):
        plan = copy.deepcopy(self.plan)
        readme = b"# Different fixture\nStill disposable.\n"
        plan["request"] = rehearsal.preview.request_for_readme(readme)
        plan["readme_bytes"] = len(readme)
        plan["readme_sha256"] = rehearsal.hashlib.sha256(readme).hexdigest()
        digest = self.resign(plan)
        with self.assertRaises(rehearsal.RehearsalStopped):
            self.run_rehearsal(plan)
        self.assertEqual(self.backend.count_requests, [])
        report = self.run_rehearsal(plan, confirmed_digest=digest)
        self.assertEqual(report["plan_sha256"], digest)

    def test_tools_storage_tier_output_model_and_extra_input_overrides_rejected(self):
        for key, value in (
            ("tools", [{"type": "function", "name": "apply_patch"}]), ("store", True),
            ("service_tier", "fast"), ("max_output_tokens", 513), ("model", "another-model"),
            ("previous_response_id", "unexpected"), ("reasoning", {"effort": "high"}),
            ("input", [{"role": "user", "content": "README source data (JSON string):\n\"hello\""},
                       {"role": "system", "content": "extra instructions"}]),
        ):
            plan = copy.deepcopy(self.plan)
            plan["request"][key] = value
            digest = self.resign(plan)
            with self.subTest(key=key), self.assertRaises(rehearsal.RehearsalStopped):
                self.run_rehearsal(plan, confirmed_digest=digest)
        self.assertEqual(self.backend.count_requests, [])

    def test_forged_prices_limits_retries_and_boolean_counts_are_rejected(self):
        for key, value in (
            ("generation_attempts", 2), ("automatic_retries", 1),
            ("generation_attempts", True), ("input_microusd_per_million", 0),
            ("max_exact_input_tokens", 99999), ("allowance_microusd", 500000),
        ):
            plan = copy.deepcopy(self.plan)
            plan["limits"][key] = value
            digest = self.resign(plan)
            with self.subTest(key=key, value=value), self.assertRaises(rehearsal.RehearsalStopped):
                self.run_rehearsal(plan, confirmed_digest=digest)
        self.assertEqual(self.backend.count_requests, [])

    def test_price_date_must_match_review_day(self):
        for today in (self.arguments["today"] + timedelta(days=1),
                      self.arguments["today"] - timedelta(days=1), "not-a-date"):
            with self.subTest(today=today), self.assertRaises(rehearsal.RehearsalStopped):
                self.run_rehearsal(today=today)
        self.assertEqual(self.backend.count_requests, [])

    def test_invalid_allowance_stops_before_count_and_small_allowance_before_generation(self):
        for allowance in (0, -1, True, 1.0, "20000", 20001):
            with self.subTest(allowance=allowance), self.assertRaises(rehearsal.RehearsalStopped):
                self.run_rehearsal(allowance=allowance)
        self.assertEqual(self.backend.count_requests, [])
        with self.assertRaisesRegex(rehearsal.RehearsalStopped, "no generation"):
            self.run_rehearsal(allowance=100)
        self.assertEqual(self.backend.generation_requests, [])
        self.assertEqual(self.journal.get(self.pilot_id)["state"], "SIMULATED_BLOCKED_BEFORE_GENERATION")

    def test_bad_or_oversized_token_counts_never_generate(self):
        for count in (2049, 0, -1, True, 128.0, None):
            pilot_id = str(uuid4())
            with mock.patch.object(self.backend, "count_input_tokens", return_value=count), \
                    self.subTest(count=count), self.assertRaises(rehearsal.RehearsalStopped):
                self.run_rehearsal(pilot_id=pilot_id)
            self.assertEqual(self.journal.get(pilot_id)["state"], "SIMULATED_BLOCKED_BEFORE_GENERATION")
        self.assertEqual(self.backend.generation_requests, [])

    def test_count_failure_is_sanitized_and_never_retried(self):
        with mock.patch.object(self.backend, "count_input_tokens", side_effect=TimeoutError("secret-value")) as count:
            with self.assertRaises(rehearsal.RehearsalStopped) as error:
                self.run_rehearsal()
            self.assertNotIn("secret-value", str(error.exception))
            with self.assertRaises(rehearsal.RehearsalStopped):
                self.run_rehearsal()
        self.assertEqual(count.call_count, 1)
        self.assertEqual(self.backend.generation_requests, [])

    def test_generation_is_blocked_when_journal_cannot_commit_start(self):
        original_update = self.journal.update
        def fail_start(pilot_id, state, **values):
            if state == "SIMULATED_GENERATION_STARTED":
                raise sqlite3.OperationalError("fixture journal unavailable")
            original_update(pilot_id, state, **values)
        with mock.patch.object(self.journal, "update", side_effect=fail_start):
            with self.assertRaises(sqlite3.OperationalError):
                self.run_rehearsal()
        self.assertEqual(self.backend.generation_requests, [])
        with self.assertRaises(rehearsal.RehearsalStopped):
            self.run_rehearsal(journal=rehearsal.AttemptJournal(self.journal_path))

    def test_counter_cannot_modify_generation_request(self):
        def counter(request):
            request["tools"] = [{"name": "execute_command"}]
            request["max_output_tokens"] = 999999
            return 128
        with mock.patch.object(self.backend, "count_input_tokens", side_effect=counter):
            self.run_rehearsal()
        self.assertEqual(self.backend.generation_requests[0], self.plan["request"])

    def test_reservation_is_committed_before_generation_and_timeout_blocks_restart(self):
        def timeout(request):
            reopened = rehearsal.AttemptJournal(self.journal_path)
            self.assertEqual(reopened.get(self.pilot_id)["state"], "SIMULATED_GENERATION_STARTED")
            raise TimeoutError("possibly-charged-secret")
        with mock.patch.object(self.backend, "generate", side_effect=timeout) as generate:
            with self.assertRaises(rehearsal.RehearsalStopped) as error:
                self.run_rehearsal()
            self.assertNotIn("possibly-charged-secret", str(error.exception))
            self.assertEqual(self.journal.get(self.pilot_id)["state"], "SIMULATED_UNKNOWN_OUTCOME")
            with self.assertRaises(rehearsal.RehearsalStopped):
                self.run_rehearsal(journal=rehearsal.AttemptJournal(self.journal_path))
        self.assertEqual(generate.call_count, 1)

    def test_process_interrupt_leaves_a_durable_blocked_attempt(self):
        with mock.patch.object(self.backend, "generate", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_rehearsal()
        self.assertEqual(self.journal.get(self.pilot_id)["state"], "SIMULATED_GENERATION_STARTED")
        with self.assertRaises(rehearsal.RehearsalStopped):
            self.run_rehearsal(journal=rehearsal.AttemptJournal(self.journal_path))
        self.assertEqual(len(self.backend.count_requests), 1)

    def test_malformed_usage_or_tool_response_cannot_claim_success_or_retry(self):
        response = rehearsal.FixtureResponses().generate({})
        for changes in (
            {"output_tokens": 513}, {"output_tokens": -1}, {"input_tokens": True},
            {"input_tokens": 129}, {"output_tokens": 48.0}, {"output_tokens": None},
            {"status": "failed"}, {"output_text": "x" * 8193},
            {"tool_calls": [{"name": "apply_patch"}]},
        ):
            pilot_id = str(uuid4())
            with mock.patch.object(self.backend, "generate", return_value={**response, **changes}) as generate:
                with self.subTest(changes=changes), self.assertRaises(rehearsal.RehearsalStopped):
                    self.run_rehearsal(pilot_id=pilot_id)
                self.assertEqual(self.journal.get(pilot_id)["state"], "SIMULATED_UNKNOWN_OUTCOME")
                with self.assertRaises(rehearsal.RehearsalStopped):
                    self.run_rehearsal(pilot_id=pilot_id)
                self.assertEqual(generate.call_count, 1)

    def test_incomplete_response_is_labeled_and_not_retried(self):
        response = rehearsal.FixtureResponses().generate({})
        response["status"] = "incomplete"
        with mock.patch.object(self.backend, "generate", return_value=response) as generate:
            report = self.run_rehearsal()
            self.assertEqual(report["state"], "SIMULATED_INCOMPLETE")
            with self.assertRaises(rehearsal.RehearsalStopped):
                self.run_rehearsal()
        self.assertEqual(generate.call_count, 1)

    def test_concurrent_duplicate_reservation_allows_only_one_generation(self):
        def attempt():
            try:
                return self.run_rehearsal(journal=rehearsal.AttemptJournal(self.journal_path))["state"]
            except rehearsal.RehearsalStopped:
                return "blocked"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: attempt(), range(2)))
        self.assertCountEqual(results, ["SIMULATED_COMPLETED", "blocked"])
        self.assertEqual(len(self.backend.generation_requests), 1)

    def test_real_or_custom_backend_is_rejected_before_any_call(self):
        backend = mock.Mock()
        with self.assertRaisesRegex(rehearsal.RehearsalStopped, "built-in"):
            self.run_rehearsal(backend=backend)
        backend.count_input_tokens.assert_not_called()
        backend.generate.assert_not_called()
        self.assertIsNone(self.journal.get(self.pilot_id))

    def test_journal_contains_no_source_response_or_raw_exception_text(self):
        self.run_rehearsal()
        raw = self.journal_path.read_bytes()
        self.assertNotIn(b"Disposable repository", raw)
        self.assertNotIn(b"SIMULATED RESPONSE", raw)

    def test_cli_is_offline_even_with_inherited_key_and_removes_only_temporary_fixture(self):
        output = io.StringIO()
        before = set(self.root.iterdir())
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "must-not-be-read"}), \
                mock.patch.object(os, "getenv", side_effect=AssertionError("No credential reads")), \
                mock.patch.object(socket, "socket", side_effect=AssertionError("No network")), \
                redirect_stdout(output):
            self.assertEqual(rehearsal.main([]), 0)
        self.assertIn('"duplicate_attempt_blocked": true', output.getvalue())
        self.assertIn('"actual_charge_microusd": 0', output.getvalue())
        self.assertIn("SIMULATED", output.getvalue())
        self.assertNotIn("must-not-be-read", output.getvalue())
        self.assertEqual(set(self.root.iterdir()), before)

    def test_cli_has_no_live_option_and_help_never_opens_database(self):
        with mock.patch.object(sqlite3, "connect") as connect, \
                redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            for argv, code in ((["--help"], 0), (["--live"], 2)):
                with self.assertRaises(SystemExit) as error:
                    rehearsal.main(argv)
                self.assertEqual(error.exception.code, code)
        connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
