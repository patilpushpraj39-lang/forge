"""Free known-patch code workflow; all runs, decisions and workspaces are temporary."""
from __future__ import annotations

import hashlib
import json
import os
import runpy
import signal
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "evals/public-tasks/status-normalizer"
FIXTURE_HASHES = {
    "README.md": "d93f92a260f02bdcbc2c4c816674567ff24fc3f2803b6f13220c00b233aefc6e",
    "status.py": "463ab5c686faffcd767c4080ac282abb2369f946ee0e3e34e973f56e3dd7fce3",
    "test_status.py": "9d6fb6a976562b961f4135bbcfaf541e1be70770c97f37c914a7651475708536",
}
BOOTSTRAP = "import runpy, sys; raise SystemExit(runpy.run_path(sys.argv[1])['_fixture_child']())"


def fixture_contents() -> dict[str, bytes]:
    contents = {}
    for name, checksum in FIXTURE_HASHES.items():
        path = FIXTURE / name
        if path.is_symlink() or path.resolve().parent != FIXTURE.resolve():
            raise ValueError("Bundled fixture must not redirect to another source.")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != checksum:
            raise ValueError("Bundled fixture content changed; do not execute it.")
        contents[name] = content
    return contents


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def exercise(root: Path) -> dict:
    """Only the isolated child or scrubbed tests call this with a temporary root."""
    from contextlib import closing
    import sqlite3
    from unittest.mock import patch
    from fastapi import HTTPException
    from forge_agent_core import RunStore
    from forge_sandbox_controller import CommandStatus, LocalArtifactStore, LocalSandboxController
    from forge_worker.offline_demo import OFFLINE_DEMO_PATCH, offline_demo_evaluation_template
    import forge_api.main as api

    contents = fixture_contents()  # Validate before executing any fixture code.
    repository = root / "public-fixture"
    repository.mkdir()
    for name, content in contents.items():
        (repository / name).write_bytes(content)
    database = root / "fixture.db"
    store = RunStore(database)
    template = offline_demo_evaluation_template()
    allowed_commands = {tuple(check.command) for check in template.public_checks}
    # The diff builder omits the optional function-context text in the hunk
    # header. Both encodings describe the same fixed one-line change.
    expected_review_patch = OFFLINE_DEMO_PATCH.replace(
        '@@ -2,7 +2,7 @@ VALID_STATUSES = {"active", "paused", "disabled"}\n',
        '@@ -2,7 +2,7 @@\n',
    )

    class TrackedController(LocalSandboxController):
        def __init__(self):
            super().__init__(LocalArtifactStore(root / "artifacts"))
            self.commands = []
            self.created = set()
            self.destroyed = set()

        def create(self, source):
            require(source.resolve() == repository.resolve(), "Only the copied public fixture is allowed.")
            handle = super().create(source)
            self.created.add(handle.sandbox_id)
            return handle

        def create_from_snapshot(self, snapshot):
            handle = super().create_from_snapshot(snapshot)
            self.created.add(handle.sandbox_id)
            return handle

        def execute(self, sandbox_id, command, *args, **kwargs):
            require(tuple(command) in allowed_commands, "Unexpected fixture command.")
            # Use this exact interpreter, not an unrelated python found on PATH.
            result = super().execute(sandbox_id, [sys.executable, *command[1:]], *args, **kwargs)
            self.commands.append((sandbox_id, tuple(command), result))
            return result

        def apply_patch(self, sandbox_id, patch_text, *args, **kwargs):
            require(patch_text in {OFFLINE_DEMO_PATCH.encode("utf-8"), expected_review_patch.encode("utf-8")},
                    "Only the predetermined public patch is allowed.")
            return super().apply_patch(sandbox_id, patch_text, *args, **kwargs)

        def destroy(self, sandbox_id):
            workspace_root = self._require_sandbox(sandbox_id).root
            super().destroy(sandbox_id)
            require(not workspace_root.exists(), "Temporary workspace still exists after cleanup.")
            self.destroyed.add(sandbox_id)

    controller = TrackedController()
    outcomes = []
    patch_text = None
    patch_hash = None
    with patch.object(api, "store", store), patch.object(api, "controller", controller), \
         patch.object(api, "_offline_demo_fixture_path", return_value=repository):
        for choice in ("approved", "rejected"):
            offset = len(controller.commands)
            created = api.create_offline_demo_run()
            run_id = created["run"]["run_id"]
            review = created["review"]
            commands = controller.commands[offset:]
            require(len(commands) == 6, "Expected reproduction, repaired test and four independent checks.")
            baseline, repaired = commands[0], commands[1]
            require(baseline[2].status == CommandStatus.FAILED and baseline[2].exit_code == 1
                    and "FAIL: test_whitespace_and_case_are_normalized" in baseline[2].output
                    and "FAILED (failures=1)" in baseline[2].output
                    and not baseline[2].output_truncated, "The expected regression was not reproduced.")
            require(repaired[2].status == CommandStatus.COMPLETED and repaired[2].exit_code == 0
                    and "Ran 3 tests" in repaired[2].output, "The repaired public suite did not pass.")
            require(baseline[0] == repaired[0]
                    and all(record[0] != baseline[0] and record[2].status == CommandStatus.COMPLETED
                            and record[2].exit_code == 0 for record in commands[2:]),
                    "Independent verification must run in a fresh workspace.")
            require(created["run"]["state"] == "AWAITING_APPROVAL" and review["verdict"] == "passed"
                    and len(review["checks"]) == 4 and all(c["status"] == "passed" for c in review["checks"]),
                    "Expected a passing review, not publication or completion.")
            require(review["patch"] == expected_review_patch and review["changed_paths"] == ["status.py"]
                    and hashlib.sha256(review["patch"].encode()).hexdigest() == review["patch_hash"],
                    "Patch review integrity failed.")
            patch_text, patch_hash = review["patch"], review["patch_hash"]
            request = api.OfflineDemoDecisionRequest(decision=choice, patch_hash=patch_hash,
                evaluation_verdict_hash=review["verdict_hash"], decision_key="fixture-code-review-" + choice)

            def must_conflict(candidate):
                before = store.list_events(run_id)
                try:
                    api.record_offline_demo_decision(run_id, candidate)
                except HTTPException as error:
                    require(error.status_code == 409, "Expected an evidence conflict.")
                else:
                    raise ValueError("Conflicting evidence was accepted.")
                require(store.list_events(run_id) == before, "Conflict changed the audit trail.")

            must_conflict(request.model_copy(update={"patch_hash": "f" * 64}))
            must_conflict(request.model_copy(update={"evaluation_verdict_hash": "e" * 64}))
            decided = api.record_offline_demo_decision(run_id, request)
            replay = api.record_offline_demo_decision(run_id, request)
            with patch.object(api, "store", RunStore(database)):
                restored = api.get_offline_demo_run(run_id)
            require(replay["decision"] == restored["decision"] == decided["decision"]
                    and restored["review"] == review, "Decision did not restore or replay unchanged.")
            must_conflict(request.model_copy(update={"decision": "rejected" if choice == "approved" else "approved"}))
            require(decided["decision"]["decision"] == choice
                    and decided["decision"]["authorizes_github_write"] is False
                    and restored["run"]["state"] == "AWAITING_APPROVAL", "Record-only scope changed.")
            events = store.list_events(run_id)
            require(sum(e["event_type"] == "offline_demo_review_decided" for e in events) == 1
                    and not any(e["event_type"] in {"approval_granted", "publication_requested"} for e in events),
                    "Fixture decision created unintended authority.")
            stopped = next(e["payload"] for e in events if e["event_type"] == "agent_stopped")
            require(stopped["cost_microusd"] == 0, "Offline runtime incurred unexpected cost.")
            outcomes.append({"fixture_decision": choice, "record_only": True, "restored": True,
                             "duplicate_event_prevented": True, "conflict_blocked": True, "state": "AWAITING_APPROVAL"})
    with closing(sqlite3.connect(database)) as connection:
        for table in ("approvals", "publication_jobs"):
            require(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0,
                    "A publication authorization or job was created.")
    require(controller.created == controller.destroyed and len(controller.created) == 4,
            "Temporary execution/verification workspace cleanup failed.")
    require(fixture_contents() == contents and all((repository / name).read_bytes() == content
                for name, content in contents.items()), "The source fixture was modified.")
    return {"kind": "known-patch-code-workflow-check-v1", "workflow": "real_local_fixture_execution",
            "patch_source": "predetermined_public_fixture", "ai_used": False,
            "baseline": "expected_regression_failed", "patched_public_tests": 3,
            "independent_checks_passed": 4, "fresh_verification_workspace": True,
            "patch_sha256": patch_hash, "patch": patch_text, "fixture_decisions": outcomes,
            "temporary_workspaces_destroyed": 4, "source_unchanged": True,
            "provider_calls": 0, "charge_microusd": 0, "github_writes": 0,
            "security_sandbox_proven": False, "live_ai_quality_proven": False}


def _fixture_child() -> int:
    support = runpy.run_path(str(ROOT / "scripts/check-controlled-access.py"))
    if Path.cwd().resolve() == ROOT or any(key.upper().startswith(support["REMOVED_PREFIXES"]) for key in os.environ):
        print("CHECK - Isolated fixture environment required.")
        return 2
    for directory in (ROOT / "packages", ROOT / "services"):
        for path in directory.glob("*/src"):
            sys.path.insert(0, str(path))
    # All solver/evaluator workspace allocations stay inside the child root.
    tempfile.tempdir = str(Path.cwd())
    try:
        with support["block_network"]():
            report = exercise(Path.cwd())
        print(json.dumps(report, indent=2))
        return 0
    except Exception as error:
        print(f"CHECK - Known-patch workflow failed ({type(error).__name__}); no live run was used.")
        return 1


def stop_fixture_process(process: subprocess.Popen) -> None:
    """Stop only the checkpoint helper we launched, never a server by name."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        result = subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=10, check=False,
        )
        if result.returncode != 0 and process.poll() is None:
            raise RuntimeError("Checkpoint helper could not be stopped safely.")
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=5)


def run_isolated(command: list[str], temporary: str, environment: dict[str, str]) -> int:
    process = subprocess.Popen(
        command, cwd=temporary, env=environment,
        start_new_session=os.name != "nt",
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )
    try:
        return process.wait(timeout=60)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        stop_fixture_process(process)
        raise


def main() -> int:
    if len(sys.argv) != 1:
        print("Usage: python scripts/check-code-workflow.py (no options or repository input)")
        return 2
    support = runpy.run_path(str(ROOT / "scripts/check-controlled-access.py"))
    print("Checking a predetermined patch with temporary fixture runs, not AI.", flush=True)
    with tempfile.TemporaryDirectory(prefix="forge-code-workflow-") as temporary:
        try:
            code = run_isolated([sys.executable, "-I", "-B", "-c", BOOTSTRAP, str(Path(__file__).resolve())],
                temporary, support["checkpoint_environment"](dict(os.environ)))
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            print("CHECK - Temporary workflow could not finish; existing servers were not changed.")
            code = 1
    print("KNOWN-PATCH FIXTURE ONLY - real tests, scripted patch/decisions; not AI-quality or deployment proof.")
    print("Local temporary workspaces are not host security sandboxes. No credentials or live GitHub writes.")
    print("Temporary runs/artifacts/decisions removed. Saved source run and review receipt untouched.")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
