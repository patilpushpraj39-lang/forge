"""Execute one explicit local GitHub snapshot smoke test, without AI or publishing."""
from __future__ import annotations

import argparse
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
for relative in (
    "packages/agent-core/src", "packages/evaluation/src",
    "packages/repo-intelligence/src", "services/sandbox-controller/src",
    "services/worker/src",
):
    sys.path.insert(0, str(ROOT / relative))

from dotenv import dotenv_values
from forge_agent_core import RepositoryTarget, RunState, RunStore, SourceSnapshot
from forge_sandbox_controller import LocalArtifactStore, LocalSandboxController
from forge_worker.main import execute_claimed_run

SMOKE_OBJECTIVE = "Worker smoke test only: read the captured README. No AI, file changes, or pull request."
SAFE_ENVIRONMENT = {
    "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "PATH",
    "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL",
}
README_CHECK = (
    "import hashlib,json; from pathlib import Path; "
    "p=Path('README.md'); assert p.is_file() and not p.is_symlink(), 'README missing'; "
    "data=p.open('rb').read(2000001); assert len(data)<=2000000, 'README too large'; "
    "print(json.dumps({'check':'captured_readme','bytes':len(data),"
    "'sha256':hashlib.sha256(data).hexdigest()}))"
)


@contextmanager
def credential_free_environment():
    original = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update({key: value for key, value in original.items()
                           if key.upper() in SAFE_ENVIRONMENT})
        yield
    finally:
        os.environ.clear()
        os.environ.update(original)


def local_paths(config: dict, root: Path = ROOT) -> tuple[Path, Path]:
    if (
        config.get("FORGE_API_MODE", "development") != "development"
        or config.get("FORGE_DATABASE_URL")
        or config.get("FORGE_ARTIFACT_BACKEND", "local") != "local"
        or config.get("FORGE_SANDBOX_BACKEND", "local") != "local"
    ):
        raise ValueError("This check requires local development, SQLite and local artifacts/sandbox.")
    paths = []
    for key, default in (("FORGE_DATABASE_PATH", ".state/forge.db"),
                         ("FORGE_ARTIFACT_PATH", ".state/artifacts")):
        path = (root / (config.get(key) or default)).resolve()
        if path == root.resolve() or not path.is_relative_to(root.resolve()):
            raise ValueError("Check storage must stay inside the Forge project.")
        paths.append(path)
    if not paths[0].is_file() or not paths[1].is_dir():
        raise ValueError("Existing local API database and artifact directory are required.")
    return paths[0], paths[1]


def check_run(store, controller, run_id: str, expected_repository: str) -> dict:
    run_id = str(UUID(run_id))
    run = store.get_run(run_id)
    if run["state"] != RunState.CREATED or run.get("cancellation_requested_at"):
        raise ValueError("Use a fresh CREATED run, not a cancelled or previously started run.")
    if run["objective"] != SMOKE_OBJECTIVE:
        raise ValueError("Run task must exactly match the worker smoke-test instructions.")
    target = RepositoryTarget(
        run.get("repository_owner"), run.get("repository_name"),
        run.get("installation_id"), run.get("base_ref"), run.get("base_sha"),
    )
    repository = f"{target.owner}/{target.name}"
    if repository.casefold() != expected_repository.casefold():
        raise ValueError("Selected run does not belong to the expected test repository.")
    if run["repository_path"] != f"github://{repository}@{target.base_sha}":
        raise ValueError("Run must be bound to an immutable GitHub source revision.")
    SourceSnapshot(
        run.get("source_snapshot_sha256"), run.get("source_snapshot_size_bytes"),
        run.get("source_snapshot_media_type"),
    )
    worker_id = f"local-readme-check:{run_id}"
    claimed = store.claim_run(run_id, worker_id, lease_seconds=30)
    if claimed is None:
        raise ValueError("This run could not be claimed; no other run was selected.")
    with credential_free_environment():
        execute_claimed_run(
            store, claimed, worker_id, controller=controller,
            command=(sys.executable, "-I", "-S", "-B", "-c", README_CHECK),
            timeout_seconds=10, lease_seconds=30, model_runtime=None,
        )
    state = store.get_run(run_id)["state"]
    if state != RunState.COMPLETED:
        raise ValueError("Worker check did not complete; inspect this run's audit trail.")
    return {"run_id": run_id, "state": str(state), "scope": "README smoke command only; not AI quality or publication proof."}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--repository", required=True, help="Expected disposable owner/repository")
    args = parser.parse_args(argv)
    try:
        config_path = ROOT / ".env.reviewer.local"
        if not config_path.is_file():
            raise ValueError("Local reviewer settings are missing.")
        # Parse local settings without interpolation, use only path/profile
        # values, and never open a key or load config into the environment.
        # No network, model runtime or publisher is used.
        database, artifacts = local_paths(
            dict(dotenv_values(config_path, interpolate=False)), root=ROOT,
        )
        with credential_free_environment():
            store = RunStore(database)
            controller = LocalSandboxController(LocalArtifactStore(artifacts))
            report = check_run(store, controller, args.run_id, args.repository)
        print(f"OK - Run {report['run_id']} is {report['state']}.")
        print(report["scope"])
        print("No API keys were loaded; no paid AI calls or GitHub changes were made.")
        return 0
    except Exception as error:
        # Do not dump configuration, credentials, database URLs or tracebacks.
        print(f"CHECK - {error}" if isinstance(error, ValueError)
              else f"CHECK - {type(error).__name__}; inspect the selected run's audit trail.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
