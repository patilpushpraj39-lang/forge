"""Disabled-by-default, standalone README executor. The CLI never executes it."""
from __future__ import annotations

import argparse
import copy
import hashlib
import http.client
import importlib.util
import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

SPEC = importlib.util.spec_from_file_location(
    "readonly_pilot_guards", Path(__file__).with_name("rehearse-readonly-pilot.py"),
)
assert SPEC and SPEC.loader
guards = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guards)
preview = guards.preview
ROOT = Path(__file__).resolve().parents[1]
MAX_RESPONSE_BYTES = 64 * 1024
MAX_SUMMARY_BYTES = 8192
CONSENT_SCOPE = "readonly-readme-openai-v1"


class PilotStopped(ValueError):
    """Fixed safe reason; never display credential/source/provider exceptions."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def validate_consent(plan: dict, consent: dict, project_id: str, now: datetime) -> dict:
    """Consent is supplied by a trusted operator, not by README/model text.

    This library gate is not a user-authentication service. Future integration
    must authenticate the approving user and construct consent server-side.
    """
    try:
        if type(consent) is not dict or set(consent) != {
            "scope", "plan_sha256", "source_run_id", "repository", "base_sha",
            "project_id", "allowance_microusd", "price_reviewed_date",
            "allow_source_upload", "allow_one_paid_generation", "billing_and_model_verified",
            "acknowledge_retention", "acknowledge_conditional_estimate", "confirmed_at", "expires_at",
        }:
            raise ValueError
        approved = copy.deepcopy(consent)
        if approved["scope"] != CONSENT_SCOPE or approved["project_id"] != project_id:
            raise ValueError
        for field in ("plan_sha256", "source_run_id", "repository", "base_sha"):
            if approved[field] != plan[field]:
                raise ValueError
        for field in ("allow_source_upload", "allow_one_paid_generation", "billing_and_model_verified",
                      "acknowledge_retention", "acknowledge_conditional_estimate"):
            if approved[field] is not True:
                raise ValueError
        confirmed = datetime.fromisoformat(approved["confirmed_at"])
        expires = datetime.fromisoformat(approved["expires_at"])
        if (confirmed.utcoffset() != timedelta(0) or expires.utcoffset() != timedelta(0)
                or not confirmed <= now < expires or not timedelta(0) < expires - confirmed <= timedelta(minutes=15)
                or approved["price_reviewed_date"] != now.date().isoformat()):
            raise ValueError
        guards.validate_confirmation(plan, approved["plan_sha256"], approved["allowance_microusd"], now.date())
        return approved
    except Exception:
        raise PilotStopped("Reviewed plan, fresh consent, project or price confirmation is invalid.") from None


def verify_saved_source(plan: dict):
    """Re-verify the captured source with a read-only DB; never claim its old run."""
    try:
        if str(UUID(plan["source_run_id"])) != plan["source_run_id"]:
            raise ValueError
        database = preview.project_path(".state/forge.db", ROOT)
        artifacts = preview.project_path(".state/artifacts", ROOT)
        if not database.is_file() or not artifacts.is_dir():
            raise ValueError
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (plan["source_run_id"],)).fetchone()
        if row is None:
            raise ValueError
        verified = preview.prepare_plan(dict(row), artifacts, plan["repository"])
        if canonical(verified) != canonical(plan):
            raise ValueError
    except Exception:
        raise PilotStopped("Saved immutable README no longer matches the reviewed plan.") from None


class PilotJournal:
    """One attempt TOTAL per fixed local ledger, including failed count attempts.

    Different plan, project or source IDs cannot create another slot. No reset
    or delete method exists. Operators must preserve this file/backups: deleting
    it, another checkout or another host is outside this local guard's scope.
    """

    def __init__(self):
        raw_path = ROOT / ".state/readonly-ai-pilot.sqlite3"
        if (raw_path.is_symlink() or raw_path.is_junction() or raw_path.parent.is_symlink()
                or raw_path.parent.is_junction() or not raw_path.parent.is_dir()):
            raise PilotStopped("Fixed pilot ledger is unavailable; no provider call permitted.")
        self.path = preview.project_path(".state/readonly-ai-pilot.sqlite3", ROOT)
        if self.path.exists():
            # Never initialize a pilot table inside an unrelated existing DB.
            with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as connection:
                tables = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
                if tables != [("readonly_ai_attempt",)]:
                    raise PilotStopped("Existing pilot ledger is not recognized; no provider call permitted.")
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS readonly_ai_attempt (
                slot INTEGER PRIMARY KEY CHECK (slot = 1), plan_sha256 TEXT NOT NULL,
                project_sha256 TEXT NOT NULL, consent_sha256 TEXT NOT NULL, state TEXT NOT NULL,
                input_tokens INTEGER, output_tokens INTEGER, estimate_microusd INTEGER,
                usage_estimate_microusd INTEGER
            )""")

    def reserve(self, plan: dict, consent: dict, project_id: str):
        try:
            with closing(sqlite3.connect(self.path)) as connection, connection:
                connection.execute(
                    """INSERT INTO readonly_ai_attempt
                    (slot, plan_sha256, project_sha256, consent_sha256, state) VALUES (1, ?, ?, ?, 'RESERVED')""",
                    (plan["plan_sha256"], hashlib.sha256(project_id.encode()).hexdigest(),
                     hashlib.sha256(canonical(consent).encode()).hexdigest()),
                )
        except sqlite3.IntegrityError:
            raise PilotStopped("Pilot attempt already reserved; no replay or new attempt is permitted.") from None

    def transition(self, before: str, after: str, *, input_tokens=None, output_tokens=None,
                   estimate=None, usage_estimate=None):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            changed = connection.execute(
                """UPDATE readonly_ai_attempt SET state = ?, input_tokens = COALESCE(?, input_tokens),
                output_tokens = COALESCE(?, output_tokens), estimate_microusd = COALESCE(?, estimate_microusd),
                usage_estimate_microusd = COALESCE(?, usage_estimate_microusd) WHERE slot = 1 AND state = ?""",
                (after, input_tokens, output_tokens, estimate, usage_estimate, before),
            ).rowcount
            if changed != 1:
                raise PilotStopped("Pilot ledger transition failed; do not replay.")


def parse_json(raw: bytes) -> dict:
    def unique_fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result
    def invalid_constant(value):
        raise ValueError
    result = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_fields, parse_constant=invalid_constant)
    if type(result) is not dict:
        raise ValueError
    return result


class _OpenAIResponses:
    """Private transport; only execute() invokes it after local approval gates.

    Direct verified HTTPS to a fixed host, no SDK/default retries, proxy or
    base-URL environment settings, redirects, polling, tools or file API.
    """

    def __init__(self, api_key: str, project_id: str):
        self._headers = {"Authorization": "Bearer " + api_key, "OpenAI-Project": project_id,
                         "Content-Type": "application/json", "Accept": "application/json"}

    def post(self, path: str, body: dict) -> dict:
        if path not in {"/v1/responses/input_tokens", "/v1/responses"}:
            raise PilotStopped("Only README counting and generation endpoints are permitted.")
        connection = None
        try:
            connection = http.client.HTTPSConnection("api.openai.com", timeout=30)
            connection.request("POST", path, body=canonical(body).encode(), headers=self._headers)
            response = connection.getresponse()
            # Never follow a redirect or read/display an HTTP error body.
            if response.status != 200 or response.getheader("Content-Type", "").split(";", 1)[0].strip() != "application/json":
                raise ValueError
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError
            return parse_json(raw)
        except Exception:
            raise PilotStopped("Provider response unavailable or invalid; automatic retry is disabled.") from None
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass  # Preserve the sanitized outcome, including on cleanup failure.

    def count_input_tokens(self, request: dict) -> int:
        # The count endpoint accepts input-shaping fields, not generation-only
        # max_output_tokens/store/service_tier. Input/instructions/model/tools/
        # reasoning are unchanged from the reviewed generation request.
        body = {key: copy.deepcopy(request[key]) for key in ("model", "instructions", "input", "tools", "reasoning")}
        result = self.post("/v1/responses/input_tokens", body)
        if set(result) != {"object", "input_tokens"} or result["object"] != "response.input_tokens":
            raise PilotStopped("Exact provider input count is invalid.")
        return result["input_tokens"]

    def generate(self, request: dict) -> dict:
        return self.post("/v1/responses", request)


def validate_response(response: dict, counted: int) -> tuple[str, int, str]:
    """Reject tool/unexpected output, changed settings, malformed usage and SSE."""
    if (response.get("object") != "response" or response.get("model") != preview.MODEL
            or response.get("status") not in {"completed", "incomplete"}
            or response.get("service_tier") != "default" or response.get("store") is not False
            or response.get("tools") != [] or response.get("error") is not None
            or response.get("background", False) is not False
            or type(response.get("max_output_tokens")) is not int
            or response["max_output_tokens"] != preview.MAX_OUTPUT_TOKENS):
        raise ValueError
    usage = response["usage"]
    output_tokens = usage["output_tokens"]
    if (type(usage["input_tokens"]) is not int or usage["input_tokens"] != counted
            or type(output_tokens) is not int or not 0 <= output_tokens <= preview.MAX_OUTPUT_TOKENS
            or type(usage["total_tokens"]) is not int or usage["total_tokens"] != counted + output_tokens):
        raise ValueError
    text = []
    if type(response["output"]) is not list:
        raise ValueError
    for item in response["output"]:
        if (item["type"] != "message" or item["role"] != "assistant"
                or item["status"] not in {"completed", "incomplete"} or type(item["content"]) is not list):
            raise ValueError
        if response["status"] == "completed" and item["status"] != "completed":
            raise ValueError
        for content in item["content"]:
            if (content["type"] != "output_text" or type(content["text"]) is not str
                    or content.get("annotations", []) != []):
                raise ValueError
            text.append(content["text"])
    summary = "".join(text)
    if (len(summary.encode("utf-8")) > MAX_SUMMARY_BYTES
            or any(not char.isprintable() and char not in "\n\r\t" for char in summary)
            or (response["status"] == "completed" and (not summary.strip() or output_tokens == 0))):
        raise ValueError
    return response["status"], output_tokens, summary


def execute(plan=None, consent=None, *, enabled=False, api_key=None, project_id=None) -> dict:
    """Operator-only future entry point. Never called by web/API/worker/CLI.

    Enabling this function is not authorized by a request to build or test it.
    A future operator must separately obtain upload/spending authorization and
    supply scoped credentials explicitly. No environment/key-file lookup occurs.
    """
    if enabled is not True:
        raise PilotStopped("Live README execution is disabled by default.")
    try:
        if (type(project_id) is not str or not re.fullmatch(r"proj_[A-Za-z0-9_-]{1,100}", project_id)
                or type(api_key) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{16,512}", api_key)):
            raise ValueError
        frozen = copy.deepcopy(plan)
        approved = validate_consent(frozen, consent, project_id, utc_now())
        request = copy.deepcopy(frozen["request"])
        verify_saved_source(frozen)
        journal = PilotJournal()
        journal.reserve(frozen, approved, project_id)
    except PilotStopped:
        raise
    except Exception:
        raise PilotStopped("Local approval, credentials or ledger unavailable; no provider call made.") from None
    try:
        validate_consent(frozen, approved, project_id, utc_now())
        backend = _OpenAIResponses(api_key, project_id)
        counted = backend.count_input_tokens(copy.deepcopy(request))
        estimate = preview.check_input_budget(counted)
        if estimate > approved["allowance_microusd"]:
            raise ValueError
        journal.transition("RESERVED", "COUNTED", input_tokens=counted, estimate=estimate)
    except Exception:
        mark_failed(journal, "RESERVED", "BLOCKED_BEFORE_GENERATION")
        raise PilotStopped("Count or allowance gate failed; no generation attempted, replay blocked.") from None
    try:
        # Recheck consent/day after counting; no stale approval or silent re-price.
        validate_consent(frozen, approved, project_id, utc_now())
        journal.transition("COUNTED", "GENERATION_STARTED")
    except Exception:
        mark_failed(journal, "COUNTED", "BLOCKED_BEFORE_GENERATION")
        raise PilotStopped("Consent expired or journal commit failed; no generation attempted, replay blocked.") from None
    try:
        response = backend.generate(copy.deepcopy(request))  # Exactly once, no retries.
        status, output_tokens, summary = validate_response(response, counted)
        usage_estimate = (counted * preview.INPUT_MICROUSD_PER_MILLION
                          + output_tokens * preview.OUTPUT_MICROUSD_PER_MILLION + 999_999) // 1_000_000
        state = "COMPLETED" if status == "completed" else "INCOMPLETE"
        journal.transition("GENERATION_STARTED", state, output_tokens=output_tokens, usage_estimate=usage_estimate)
    except Exception:
        mark_failed(journal, "GENERATION_STARTED", "UNKNOWN_OUTCOME")
        raise PilotStopped("Generation outcome uncertain; inspect manually, never retry automatically.") from None
    return {"state": state, "provider_requests_attempted": 2, "generation_attempts": 1,
            "plan_sha256": frozen["plan_sha256"], "input_tokens": counted, "output_tokens": output_tokens,
            "conditional_max_estimate_microusd": estimate, "usage_estimate_microusd": usage_estimate,
            "billing_verified": False, "summary_untrusted": True, "summary": summary}


def mark_failed(journal: PilotJournal, before: str, after: str):
    try:
        journal.transition(before, after)
    except Exception:
        # A prior committed reservation/start still blocks replay if storage fails.
        pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)  # No --enable, --execute, --key or --reset switch.
    print("DISABLED - Read-only AI executor is not connected to the web/API/general worker.")
    print("No credentials read, source uploaded, provider calls, storage changes or GitHub writes.")
    print("Separate live authorization, funded project and reviewed integration are still required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
