from __future__ import annotations

from typing import Any

from forge_agent_core import RunStoreProtocol
from forge_evaluation import PatchParseError, PatchPolicy, inspect_patch
from forge_sandbox_controller import (
    ArtifactNotFoundError,
    ArtifactRef,
    InvalidPatchError,
    SandboxController,
)

from .github import GitHubPullRequestPublisher, PublisherError
from .models import GitHubChange


class PublicationEvidenceError(RuntimeError):
    pass


def run_once(
    store: RunStoreProtocol,
    controller: SandboxController,
    github: GitHubPullRequestPublisher,
    *,
    publisher_id: str = "publisher-local",
    lease_seconds: float = 30,
) -> bool:
    publication = store.claim_publication(publisher_id, lease_seconds)
    if publication is None:
        return False
    publication_id = str(publication["publication_id"])
    try:
        snapshot, patch = _artifact_evidence(
            store, str(publication["run_id"]), str(publication["patch_hash"])
        )
        patch_bytes = controller.read_artifact(
            patch, max_bytes=max(1, patch.size_bytes)
        )
        stats, findings = inspect_patch(patch_bytes, PatchPolicy())
        if findings or not stats.changed_paths:
            raise PublicationEvidenceError(
                "stored patch no longer satisfies publication policy"
            )
        sandbox = controller.create_from_snapshot(snapshot)
        try:
            controller.apply_patch(sandbox.sandbox_id, patch_bytes, patch.sha256)
            changes = tuple(
                _github_change(controller, sandbox.sandbox_id, path)
                for path in stats.changed_paths
            )
        finally:
            controller.destroy(sandbox.sandbox_id)

        def heartbeat() -> None:
            store.renew_publication_lease(
                publication_id, publisher_id, lease_seconds
            )

        head_sha = github.prepare_branch(publication, changes, heartbeat)
        publication = store.record_publication_head(
            publication_id, publisher_id, head_sha
        )
        result = github.ensure_pull_request(publication, head_sha, heartbeat)
        store.complete_publication(
            publication_id,
            publisher_id,
            result.number,
            result.url,
            result.head_sha,
        )
        return True
    except PublisherError as error:
        store.fail_publication(
            publication_id,
            publisher_id,
            error.code,
            permanent=error.permanent,
        )
        return True
    except (
        ArtifactNotFoundError,
        InvalidPatchError,
        PatchParseError,
        PublicationEvidenceError,
    ):
        store.fail_publication(
            publication_id,
            publisher_id,
            "invalid_publication_evidence",
            permanent=True,
        )
        return True


def _artifact_evidence(
    store: RunStoreProtocol, run_id: str, patch_hash: str
) -> tuple[ArtifactRef, ArtifactRef]:
    for event in reversed(store.list_events(run_id)):
        if event["event_type"] != "evaluation_started":
            continue
        payload = event["payload"]
        patch_payload = payload.get("patch_artifact")
        snapshot_payload = payload.get("snapshot_artifact")
        if not isinstance(patch_payload, dict) or not isinstance(
            snapshot_payload, dict
        ):
            continue
        patch = _artifact_ref(patch_payload)
        if patch.sha256 != patch_hash:
            continue
        return _artifact_ref(snapshot_payload), patch
    raise PublicationEvidenceError(
        "approved evaluation artifacts were not found in the run audit log"
    )


def _artifact_ref(value: dict[str, Any]) -> ArtifactRef:
    try:
        return ArtifactRef(
            sha256=str(value["sha256"]),
            size_bytes=int(value["size_bytes"]),
            media_type=str(value["media_type"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise PublicationEvidenceError("artifact reference is invalid") from error


def _github_change(
    controller: SandboxController, sandbox_id: str, path: str
) -> GitHubChange:
    workspace_file = controller.read_workspace_file(
        sandbox_id, path, max_bytes=10_000_000
    )
    if workspace_file is None:
        return GitHubChange(path, None)
    return GitHubChange(
        workspace_file.path,
        workspace_file.content,
        workspace_file.executable,
    )
