from __future__ import annotations

import argparse
import os
import socket
from pathlib import Path
from typing import Sequence

from forge_agent_core import RunStoreProtocol, create_run_store
from forge_agent_core.run_store import RunState, TERMINAL_STATES
from forge_sandbox_controller import (
    CommandStatus,
    SandboxController,
    create_sandbox_controller,
)


DEFAULT_COMMAND = ("node", "--version")


def execute_bounded_command(
    store: RunStoreProtocol,
    run_id: str,
    worker_id: str,
    controller: SandboxController,
    sandbox_id: str,
    command: Sequence[str],
    timeout_seconds: float,
    lease_seconds: float,
) -> CommandStatus:
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
    result = controller.execute(
        sandbox_id,
        rendered_command,
        timeout_seconds,
        should_cancel=lambda: store.is_cancellation_requested(run_id),
        heartbeat=lambda: store.renew_lease(
            run_id, worker_id, lease_seconds
        ),
        heartbeat_interval_seconds=max(0.05, lease_seconds / 3),
    )

    if result.status == CommandStatus.CANCELLED:
        store.append_event(
            run_id,
            "command_cancelled",
            "worker",
            {"output": result.output},
        )
        store.acknowledge_cancellation(run_id, worker_id)
        return result.status

    if result.status == CommandStatus.TIMED_OUT:
        store.append_event(
            run_id,
            "command_timed_out",
            "worker",
            {"timeout_seconds": timeout_seconds, "output": result.output},
        )
        store.transition(
            run_id,
            RunState.EXECUTING,
            RunState.FAILED,
            "worker",
            {"reason": "command_timed_out"},
            lease_owner=worker_id,
        )
        return result.status

    if result.status == CommandStatus.OUTPUT_LIMIT:
        store.append_event(
            run_id,
            "command_output_limited",
            "worker",
            {"output": result.output, "output_truncated": True},
        )
        store.transition(
            run_id,
            RunState.EXECUTING,
            RunState.FAILED,
            "worker",
            {"reason": "command_output_limit"},
            lease_owner=worker_id,
        )
        return result.status

    if result.status == CommandStatus.RESOURCE_LIMIT:
        store.append_event(
            run_id,
            "command_resource_limited",
            "worker",
            {"output": result.output},
        )
        store.transition(
            run_id,
            RunState.EXECUTING,
            RunState.FAILED,
            "worker",
            {"reason": "command_resource_limit"},
            lease_owner=worker_id,
        )
        return result.status

    store.append_event(
        run_id,
        "command_completed",
        "worker",
        {
            "exit_code": result.exit_code,
            "output": result.output,
            "output_truncated": result.output_truncated,
        },
    )
    if result.status == CommandStatus.FAILED:
        store.transition(
            run_id,
            RunState.EXECUTING,
            RunState.FAILED,
            "worker",
            {"reason": "command_failed"},
            lease_owner=worker_id,
        )
    return result.status


def execute_claimed_run(
    store: RunStoreProtocol,
    run: dict[str, object],
    worker_id: str,
    command: Sequence[str] = DEFAULT_COMMAND,
    timeout_seconds: float = 10,
    lease_seconds: float = 30,
    controller: SandboxController | None = None,
) -> None:
    run_id = str(run["run_id"])
    repository_path = Path(str(run["repository_path"]))
    sandbox_id: str | None = None
    sandbox_controller = controller or create_sandbox_controller()
    try:
        if store.is_cancellation_requested(run_id):
            store.acknowledge_cancellation(run_id, worker_id)
            return

        sandbox = sandbox_controller.create(repository_path)
        sandbox_id = sandbox.sandbox_id
        store.append_event(
            run_id,
            "snapshot_ready",
            "worker",
            {
                "sandbox_id": sandbox.sandbox_id,
                "snapshot_hash": sandbox.snapshot_hash,
                "file_count": sandbox.file_count,
                "snapshot_artifact": (
                    sandbox.snapshot_artifact.to_dict()
                    if sandbox.snapshot_artifact is not None
                    else None
                ),
            },
        )
        manifest = sandbox_controller.index_repository(sandbox.sandbox_id)
        store.append_event(
            run_id,
            "repository_indexed",
            "worker",
            {"brief": manifest.brief()},
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
            sandbox_controller,
            sandbox.sandbox_id,
            command,
            timeout_seconds,
            lease_seconds,
        )
        if outcome == CommandStatus.COMPLETED:
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
        if sandbox_id is not None:
            sandbox_controller.destroy(sandbox_id)
            store.append_event(
                run_id,
                "workspace_destroyed",
                "worker",
                {"sandbox_id": sandbox_id},
            )


def run_once(
    store: RunStoreProtocol,
    worker_id: str = "worker-test",
    command: Sequence[str] = DEFAULT_COMMAND,
    timeout_seconds: float = 10,
    lease_seconds: float = 30,
    controller: SandboxController | None = None,
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
        controller=controller,
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database",
        default=os.environ.get("FORGE_DATABASE_PATH", ".state/forge.db"),
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("FORGE_DATABASE_URL"),
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--worker-id")
    args = parser.parse_args()
    store = create_run_store(
        database_url=args.database_url,
        sqlite_path=args.database,
    )
    worker_id = args.worker_id or f"{socket.gethostname()}:{os.getpid()}"

    if args.once:
        return 0 if run_once(store, worker_id=worker_id) else 2

    while run_once(store, worker_id=worker_id):
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
