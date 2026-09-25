from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Sequence

from forge_agent_core.run_store import RunState, RunStore, TERMINAL_STATES


DEFAULT_COMMAND = ("node", "--version")


def snapshot_repository(source: Path, destination: Path) -> tuple[str, int]:
    ignored = shutil.ignore_patterns(
        ".git",
        ".next",
        ".state",
        ".venv",
        ".artifacts",
        ".sandboxes",
        "__pycache__",
        "dist",
        "node_modules",
    )
    shutil.copytree(source, destination, ignore=ignored)
    digest = hashlib.sha256()
    file_count = 0
    for path in sorted(item for item in destination.rglob("*") if item.is_file()):
        relative = path.relative_to(destination).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
        file_count += 1
    return digest.hexdigest(), file_count


def terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def execute_bounded_command(
    store: RunStore,
    run_id: str,
    worker_id: str,
    workspace: Path,
    command: Sequence[str],
    timeout_seconds: float,
    lease_seconds: float,
    poll_interval: float = 0.05,
) -> str:
    rendered_command = list(command)
    store.append_event(
        run_id,
        "command_started",
        "worker",
        {
            "command": rendered_command,
            "cwd": ".",
            "timeout_seconds": timeout_seconds,
        },
    )
    process = subprocess.Popen(
        rendered_command,
        cwd=workspace,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    started_at = time.monotonic()
    next_heartbeat = started_at + max(0.05, lease_seconds / 3)

    while process.poll() is None:
        if store.is_cancellation_requested(run_id):
            terminate_process(process)
            output, _ = process.communicate(timeout=1)
            store.append_event(
                run_id,
                "command_cancelled",
                "worker",
                {"output": output[:4096]},
            )
            store.acknowledge_cancellation(run_id, worker_id)
            return "cancelled"

        now = time.monotonic()
        if now - started_at >= timeout_seconds:
            terminate_process(process)
            output, _ = process.communicate(timeout=1)
            store.append_event(
                run_id,
                "command_timed_out",
                "worker",
                {"timeout_seconds": timeout_seconds, "output": output[:4096]},
            )
            store.transition(
                run_id,
                RunState.EXECUTING,
                RunState.FAILED,
                "worker",
                {"reason": "command_timed_out"},
                lease_owner=worker_id,
            )
            return "timed_out"

        if now >= next_heartbeat:
            store.renew_lease(run_id, worker_id, lease_seconds)
            next_heartbeat = now + max(0.05, lease_seconds / 3)
        time.sleep(poll_interval)

    output, _ = process.communicate(timeout=1)
    store.append_event(
        run_id,
        "command_completed",
        "worker",
        {"exit_code": process.returncode, "output": output[:4096]},
    )
    if process.returncode != 0:
        store.transition(
            run_id,
            RunState.EXECUTING,
            RunState.FAILED,
            "worker",
            {"reason": "command_failed"},
            lease_owner=worker_id,
        )
        return "failed"
    return "completed"


def execute_claimed_run(
    store: RunStore,
    run: dict[str, object],
    worker_id: str,
    command: Sequence[str] = DEFAULT_COMMAND,
    timeout_seconds: float = 10,
    lease_seconds: float = 30,
) -> None:
    run_id = str(run["run_id"])
    repository_path = Path(str(run["repository_path"]))
    workspace_created = False
    try:
        if store.is_cancellation_requested(run_id):
            store.acknowledge_cancellation(run_id, worker_id)
            return

        with tempfile.TemporaryDirectory(prefix=f"forge-{run_id}-") as temp:
            workspace_created = True
            workspace = Path(temp) / "repository"
            snapshot_hash, file_count = snapshot_repository(repository_path, workspace)
            store.append_event(
                run_id,
                "snapshot_ready",
                "worker",
                {"snapshot_hash": snapshot_hash, "file_count": file_count},
            )
            if store.is_cancellation_requested(run_id):
                store.acknowledge_cancellation(run_id, worker_id)
                return

            store.transition(
                run_id,
                RunState.SNAPSHOTTING,
                RunState.EXECUTING,
                "worker",
                lease_owner=worker_id,
            )
            outcome = execute_bounded_command(
                store,
                run_id,
                worker_id,
                workspace,
                command,
                timeout_seconds,
                lease_seconds,
            )
            if outcome == "completed":
                if store.is_cancellation_requested(run_id):
                    store.acknowledge_cancellation(run_id, worker_id)
                    return
                store.transition(
                    run_id,
                    RunState.EXECUTING,
                    RunState.COMPLETED,
                    "worker",
                    lease_owner=worker_id,
                )
    except Exception as error:
        current = RunState(store.get_run(run_id)["state"])
        if current not in TERMINAL_STATES:
            store.transition(
                run_id,
                current,
                RunState.FAILED,
                "worker",
                {"reason": type(error).__name__, "message": str(error)[:512]},
                lease_owner=worker_id,
            )
        raise
    finally:
        if workspace_created:
            store.append_event(run_id, "workspace_destroyed", "worker", {})


def run_once(
    store: RunStore,
    worker_id: str = "worker-test",
    command: Sequence[str] = DEFAULT_COMMAND,
    timeout_seconds: float = 10,
    lease_seconds: float = 30,
) -> bool:
    run = store.claim_next_run(worker_id, lease_seconds)
    if run is None:
        return False
    execute_claimed_run(
        store,
        run,
        worker_id,
        command=command,
        timeout_seconds=timeout_seconds,
        lease_seconds=lease_seconds,
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database",
        default=os.environ.get("FORGE_DATABASE_PATH", ".state/forge.db"),
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--worker-id")
    args = parser.parse_args()
    store = RunStore(args.database)
    worker_id = args.worker_id or f"{socket.gethostname()}:{os.getpid()}"

    if args.once:
        return 0 if run_once(store, worker_id=worker_id) else 2

    while run_once(store, worker_id=worker_id):
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
