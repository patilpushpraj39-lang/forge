from __future__ import annotations

import argparse
import os
import socket
from pathlib import Path
from typing import Sequence

from forge_agent_core import (
    IdempotencyLedger,
    ModelPricing,
    ModelRuntime,
    OpenAIResponsesRuntime,
    PostgresIdempotencyLedger,
    RunStoreProtocol,
    SourceSnapshot,
    SqliteIdempotencyLedger,
    create_run_store,
)
from forge_agent_core.run_store import RunState, TERMINAL_STATES
from forge_sandbox_controller import (
    ArtifactRef,
    CommandStatus,
    SandboxController,
    create_sandbox_controller,
)

from .agent_execution import execute_agent_loop
from .evaluation_execution import (
    EvaluationTemplate,
    execute_independent_evaluation,
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
    model_runtime: ModelRuntime | None = None,
    idempotency_ledger: IdempotencyLedger | None = None,
    evaluation_template: EvaluationTemplate | None = None,
) -> None:
    run_id = str(run["run_id"])
    repository_path = Path(str(run["repository_path"]))
    sandbox_id: str | None = None
    sandbox_controller = controller or create_sandbox_controller()
    try:
        if store.is_cancellation_requested(run_id):
            store.acknowledge_cancellation(run_id, worker_id)
            return

        snapshot_sha = run.get("source_snapshot_sha256")
        if snapshot_sha is None:
            sandbox = sandbox_controller.create(repository_path)
        else:
            snapshot = SourceSnapshot(
                str(snapshot_sha),
                int(run["source_snapshot_size_bytes"]),
                str(run["source_snapshot_media_type"]),
            )
            sandbox = sandbox_controller.create_from_snapshot(
                ArtifactRef(
                    snapshot.sha256,
                    snapshot.size_bytes,
                    snapshot.media_type,
                )
            )
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
        if model_runtime is not None:
            if idempotency_ledger is None:
                raise ValueError(
                    "agent execution requires a durable idempotency ledger"
                )
            evidence = execute_agent_loop(
                store,
                run,
                worker_id,
                sandbox_controller,
                sandbox.sandbox_id,
                model_runtime,
                idempotency_ledger,
                lease_seconds=lease_seconds,
            )
            if evidence.patch_artifact is not None and evidence.changed_paths:
                execute_independent_evaluation(
                    store,
                    run,
                    worker_id,
                    sandbox_controller,
                    sandbox,
                    manifest,
                    evidence,
                    lease_seconds=lease_seconds,
                    template=evaluation_template,
                )
            return
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
    model_runtime: ModelRuntime | None = None,
    idempotency_ledger: IdempotencyLedger | None = None,
    evaluation_template: EvaluationTemplate | None = None,
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
        model_runtime=model_runtime,
        idempotency_ledger=idempotency_ledger,
        evaluation_template=evaluation_template,
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
    parser.add_argument(
        "--agent",
        action="store_true",
        help="run the bounded model/tool loop instead of the smoke command",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("FORGE_OPENAI_MODEL"),
    )
    parser.add_argument(
        "--input-price",
        type=int,
        default=_optional_int("FORGE_MODEL_INPUT_MICROUSD_PER_MILLION"),
    )
    parser.add_argument(
        "--cached-input-price",
        type=int,
        default=_optional_int(
            "FORGE_MODEL_CACHED_INPUT_MICROUSD_PER_MILLION"
        ),
    )
    parser.add_argument(
        "--output-price",
        type=int,
        default=_optional_int("FORGE_MODEL_OUTPUT_MICROUSD_PER_MILLION"),
    )
    parser.add_argument("--worker-id")
    args = parser.parse_args()
    store = create_run_store(
        database_url=args.database_url,
        sqlite_path=args.database,
    )
    worker_id = args.worker_id or f"{socket.gethostname()}:{os.getpid()}"
    runtime: ModelRuntime | None = None
    ledger: IdempotencyLedger | None = None
    if args.agent:
        if not args.model:
            parser.error("--agent requires --model or FORGE_OPENAI_MODEL")
        prices = (args.input_price, args.cached_input_price, args.output_price)
        if any(value is None for value in prices):
            parser.error(
                "--agent requires input, cached-input, and output prices"
            )
        runtime = OpenAIResponsesRuntime(
            args.model,
            ModelPricing(
                int(args.input_price),
                int(args.cached_input_price),
                int(args.output_price),
            ),
        )
        if args.database_url:
            migration_directory = os.environ.get(
                "FORGE_MIGRATIONS_PATH", "db/migrations"
            )
            ledger = PostgresIdempotencyLedger(
                args.database_url, migration_directory
            )
        else:
            ledger = SqliteIdempotencyLedger(args.database)

    def advance() -> bool:
        return run_once(
            store,
            worker_id=worker_id,
            model_runtime=runtime,
            idempotency_ledger=ledger,
        )

    try:
        if args.once:
            return 0 if advance() else 2
        while advance():
            pass
        return 0
    finally:
        for resource in (ledger, store):
            close = getattr(resource, "close", None)
            if callable(close):
                close()


def _optional_int(name: str) -> int | None:
    value = os.environ.get(name)
    return None if value is None else int(value)


if __name__ == "__main__":
    raise SystemExit(main())
