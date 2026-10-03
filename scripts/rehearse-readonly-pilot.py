"""Run a fixture-only AI safeguard rehearsal. No API keys or network are used."""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import io
import json
import sqlite3
import tarfile
import tempfile
from contextlib import closing
from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

SPEC = importlib.util.spec_from_file_location(
    "readonly_pilot_preview", Path(__file__).with_name("prepare-readonly-pilot.py"),
)
assert SPEC and SPEC.loader
preview = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preview)


class RehearsalStopped(ValueError):
    """Safe fixed reason; never attach raw transport errors or model content."""


class FixtureResponses:
    """Built-in fake, not an OpenAI adapter or a real tokenizer."""

    def __init__(self):
        self.count_requests = []
        self.generation_requests = []

    def count_input_tokens(self, request: dict) -> int:
        self.count_requests.append(copy.deepcopy(request))
        return 128  # Deliberately simulated; never presented as a real token count.

    def generate(self, request: dict) -> dict:
        self.generation_requests.append(copy.deepcopy(request))
        return {
            "status": "completed", "input_tokens": 128, "output_tokens": 48,
            "output_text": "SIMULATED RESPONSE: This fixture is a disposable test repository.",
        }


class AttemptJournal:
    """Local rehearsal ledger, separate from Forge's run database.

    Reserve/commit before counting. A crash or ambiguous generation is never
    automatically retried. Only hashes, fixed statuses and numeric usage are
    persisted, not source text, provider output, credentials or raw errors.
    """

    def __init__(self, path: Path):
        self.path = path
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS readonly_rehearsals (
                pilot_id TEXT PRIMARY KEY, plan_sha256 TEXT NOT NULL,
                state TEXT NOT NULL, input_tokens INTEGER, output_tokens INTEGER,
                estimate_microusd INTEGER, simulated INTEGER NOT NULL CHECK (simulated = 1)
            )""")

    def reserve(self, pilot_id: str, digest: str):
        try:
            with closing(sqlite3.connect(self.path)) as connection, connection:
                connection.execute(
                    "INSERT INTO readonly_rehearsals (pilot_id, plan_sha256, state, simulated) VALUES (?, ?, ?, 1)",
                    (pilot_id, digest, "SIMULATED_RESERVED"),
                )
        except sqlite3.IntegrityError:
            raise RehearsalStopped("Attempt already reserved; automatic replay is blocked.") from None

    def update(self, pilot_id: str, state: str, *, input_tokens=None, output_tokens=None, estimate=None):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            changed = connection.execute(
                """UPDATE readonly_rehearsals SET state = ?,
                input_tokens = COALESCE(?, input_tokens), output_tokens = COALESCE(?, output_tokens),
                estimate_microusd = COALESCE(?, estimate_microusd) WHERE pilot_id = ?""",
                (state, input_tokens, output_tokens, estimate, pilot_id),
            ).rowcount
            if changed != 1:
                raise RehearsalStopped("Attempt journal is unavailable; do not replay.")

    def get(self, pilot_id: str) -> dict | None:
        with closing(sqlite3.connect(self.path)) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM readonly_rehearsals WHERE pilot_id = ?", (pilot_id,),
            ).fetchone()
        return dict(row) if row else None


def validate_confirmation(plan: dict, confirmed_digest: str, allowance: int, today: date) -> dict:
    """Validate a frozen preview and simulated confirmation before any fake call."""
    try:
        frozen = copy.deepcopy(plan)
        digest = preview.plan_digest(frozen)
        if frozen["plan_sha256"] != digest or confirmed_digest != digest:
            raise ValueError
        if frozen["kind"] != "offline-preview-only":
            raise ValueError
        if type(allowance) is not int or not 0 < allowance <= preview.ALLOWANCE_MICROUSD:
            raise ValueError
        # Strict JSON comparisons distinguish bools from integers and refuse
        # new request fields, tools, tiers, limits, storage or retry overrides.
        canonical = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"))
        if canonical(frozen["limits"]) != canonical(preview.pilot_limits()):
            raise ValueError
        price_date = date.fromisoformat(frozen["limits"]["price_date"])
        if price_date != today:
            raise ValueError
        request = frozen["request"]
        source = request["input"][0]["content"].split("\n", 1)[1]
        text = json.loads(source)
        if not isinstance(text, str):
            raise ValueError
        readme = text.encode("utf-8")
        if canonical(request) != canonical(preview.request_for_readme(readme)):
            raise ValueError
        if (type(frozen["readme_bytes"]) is not int or frozen["readme_bytes"] != len(readme)
                or frozen["readme_sha256"] != hashlib.sha256(readme).hexdigest()):
            raise ValueError
        return request
    except (KeyError, IndexError, TypeError, ValueError, AttributeError, UnicodeError):
        raise RehearsalStopped("Preview, confirmation, limits or price date are invalid; prepare and review again.") from None


def rehearse(plan: dict, backend: FixtureResponses, journal: AttemptJournal, *,
             pilot_id: str, confirmed_digest: str, allowance: int, today: date) -> dict:
    # This free-development lane intentionally rejects SDKs/live adapters.
    if type(backend) is not FixtureResponses:
        raise RehearsalStopped("Only the built-in simulated backend is permitted.")
    pilot_id = str(UUID(pilot_id))
    request = validate_confirmation(plan, confirmed_digest, allowance, today)
    journal.reserve(pilot_id, confirmed_digest)
    try:
        # Separate copies guarantee a counter cannot rewrite the generation input.
        counted = backend.count_input_tokens(copy.deepcopy(request))
        estimate = preview.check_input_budget(counted)
        if estimate > allowance:
            raise RehearsalStopped("Counted request exceeds the confirmed allowance.")
        journal.update(pilot_id, "SIMULATED_COUNTED", input_tokens=counted, estimate=estimate)
    except Exception:
        journal.update(pilot_id, "SIMULATED_BLOCKED_BEFORE_GENERATION")
        raise RehearsalStopped("Input count or allowance gate failed; no generation was attempted.") from None
    # Commit before the call. Interrupts leave this state reserved across restart.
    journal.update(pilot_id, "SIMULATED_GENERATION_STARTED")
    try:
        response = backend.generate(copy.deepcopy(request))  # No retry loop.
        if type(response) is not dict or set(response) != {"status", "input_tokens", "output_tokens", "output_text"}:
            raise ValueError
        output = response["output_tokens"]
        if (response["status"] not in {"completed", "incomplete"}
                or type(response["input_tokens"]) is not int or response["input_tokens"] != counted
                or type(output) is not int or not 0 <= output <= preview.MAX_OUTPUT_TOKENS
                or not isinstance(response["output_text"], str)
                or len(response["output_text"].encode("utf-8")) > 8192):
            raise ValueError
        hypothetical_cost = (
            counted * preview.INPUT_MICROUSD_PER_MILLION
            + output * preview.OUTPUT_MICROUSD_PER_MILLION + 999_999
        ) // 1_000_000
        state = "SIMULATED_COMPLETED" if response["status"] == "completed" else "SIMULATED_INCOMPLETE"
        journal.update(pilot_id, state, output_tokens=output)
    except Exception:
        journal.update(pilot_id, "SIMULATED_UNKNOWN_OUTCOME")
        raise RehearsalStopped("Generation outcome needs inspection; automatic retry is blocked.") from None
    return {
        "state": state, "simulated": True, "actual_provider_calls": 0,
        "actual_charge_microusd": 0, "simulated_input_tokens": counted,
        "simulated_output_tokens": output, "conditional_max_estimate_microusd": estimate,
        "hypothetical_usage_cost_microusd": hypothetical_cost,
        "fake_generation_attempts": len(backend.generation_requests),
        "plan_sha256": confirmed_digest,
    }


def fixture_plan(root: Path) -> dict:
    """Public fixed fixture, never a user's saved run or private repository."""
    readme = b"# offline-fixture\nDisposable repository for testing only.\n"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        entry = tarfile.TarInfo("README.md")
        entry.size = len(readme)
        archive.addfile(entry, io.BytesIO(readme))
    content = buffer.getvalue()
    checksum = hashlib.sha256(content).hexdigest()
    artifacts = root / "fixture-artifacts"
    path = artifacts / "sha256" / checksum[:2] / checksum
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    run = {
        "run_id": "00000000-0000-0000-0000-000000000001", "state": "COMPLETED",
        "objective": preview.SOURCE_OBJECTIVE,
        "repository_owner": "offline", "repository_name": "fixture",
        "base_sha": "a" * 40, "repository_path": "github://offline/fixture@" + "a" * 40,
        "source_snapshot_sha256": checksum, "source_snapshot_size_bytes": len(content),
        "source_snapshot_media_type": "application/vnd.forge.snapshot+tar",
    }
    return preview.prepare_plan(run, artifacts, "offline/fixture")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)  # No live/execute/key/repository/run options exist.
    try:
        with tempfile.TemporaryDirectory(prefix="forge-readonly-rehearsal-") as temporary:
            root = Path(temporary)
            plan = fixture_plan(root)
            backend = FixtureResponses()
            journal = AttemptJournal(root / "rehearsal.db")
            pilot_id = str(uuid4())
            arguments = {"pilot_id": pilot_id, "confirmed_digest": plan["plan_sha256"],
                         "allowance": preview.ALLOWANCE_MICROUSD,
                         "today": date.fromisoformat(preview.PRICE_DATE)}
            report = rehearse(plan, backend, journal, **arguments)
            # Reopen the durable ledger and show that an identical repeat is blocked.
            reopened = AttemptJournal(root / "rehearsal.db")
            try:
                rehearse(plan, backend, reopened, **arguments)
            except RehearsalStopped:
                report["duplicate_attempt_blocked"] = len(backend.generation_requests) == 1
            else:
                raise RehearsalStopped("Duplicate safeguard failed.")
            print(json.dumps(report, indent=2))
        print("FREE REHEARSAL ONLY - Fake tokens and response, not AI-quality or billing proof.")
        print("No API keys, network requests, Forge run updates, file edits in a repository or GitHub writes.")
        print("Temporary fixture and rehearsal ledger removed. Live AI execution remains unavailable.")
        return 0
    except Exception:
        print("CHECK - Free rehearsal failed; no real provider was used. Run the offline tests for details.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
