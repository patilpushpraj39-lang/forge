from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from forge_agent_core.run_store import RunState, RunStore


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


def execute_claimed_run(store: RunStore, run: dict[str, object]) -> None:
    run_id = str(run["run_id"])
    repository_path = Path(str(run["repository_path"]))
    try:
        with tempfile.TemporaryDirectory(prefix=f"forge-{run_id}-") as temp:
            workspace = Path(temp) / "repository"
            snapshot_hash, file_count = snapshot_repository(repository_path, workspace)
            store.append_event(
                run_id,
                "snapshot_ready",
                "worker",
                {"snapshot_hash": snapshot_hash, "file_count": file_count},
            )
            store.transition(
                run_id, RunState.SNAPSHOTTING, RunState.EXECUTING, "worker"
            )
            command = ["node", "--version"]
            store.append_event(
                run_id,
                "command_started",
                "worker",
                {"command": command, "cwd": ".", "timeout_seconds": 10},
            )
            completed = subprocess.run(
                command,
                cwd=workspace,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=10,
                check=False,
            )
            output = completed.stdout[:4096]
            store.append_event(
                run_id,
                "command_completed",
                "worker",
                {"exit_code": completed.returncode, "output": output},
            )
            if completed.returncode != 0:
                store.transition(
                    run_id,
                    RunState.EXECUTING,
                    RunState.FAILED,
                    "worker",
                    {"reason": "command_failed"},
                )
            else:
                store.transition(
                    run_id, RunState.EXECUTING, RunState.COMPLETED, "worker"
                )
        store.append_event(run_id, "workspace_destroyed", "worker", {})
    except Exception as error:
        current = RunState(store.get_run(run_id)["state"])
        if current not in {RunState.COMPLETED, RunState.CANCELLED, RunState.FAILED}:
            store.transition(
                run_id,
                current,
                RunState.FAILED,
                "worker",
                {"reason": type(error).__name__, "message": str(error)[:512]},
            )
        raise


def run_once(store: RunStore) -> bool:
    run = store.claim_next_created_run()
    if run is None:
        return False
    execute_claimed_run(store, run)
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database",
        default=os.environ.get("FORGE_DATABASE_PATH", ".state/forge.db"),
    )
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    store = RunStore(args.database)

    if args.once:
        return 0 if run_once(store) else 2

    while run_once(store):
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
