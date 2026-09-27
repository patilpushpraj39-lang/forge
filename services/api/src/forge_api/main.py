from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from forge_agent_core import (
    ApprovalError,
    ApprovalExpiredError,
    BudgetLimits,
    PublicationError,
    RepositoryTarget,
    RunNotFoundError,
    SourceSnapshot,
    create_run_store,
)
from forge_evaluation import BenchmarkRecord, load_benchmark_jsonl, summarize_benchmark
from forge_publisher import (
    GitHubAppInstallationTokenProvider,
    GitHubCatalog,
    PublisherConflictError,
    PublisherPermissionError,
    PublisherTransientError,
    UrllibGitHubTransport,
)
from forge_sandbox_controller import (
    ArtifactNotFoundError,
    ArtifactRef,
    create_sandbox_controller,
)

from .auth import (
    ReviewerAuthenticationError,
    ReviewerAuthenticationUnavailable,
    ReviewerAuthenticator,
    ReviewerAuthorizationError,
    ReviewerPrincipal,
    create_reviewer_authenticator,
)


store = create_run_store()
controller = create_sandbox_controller()
reviewer_authenticator: ReviewerAuthenticator = create_reviewer_authenticator()


def _configured_github_catalog() -> GitHubCatalog | None:
    client_id = os.environ.get("FORGE_GITHUB_APP_CLIENT_ID")
    private_key_path = os.environ.get("FORGE_GITHUB_APP_PRIVATE_KEY_PATH")
    if not client_id and not private_key_path:
        return None
    if not client_id or not private_key_path:
        raise RuntimeError("GitHub App client ID and private key path must be set together")
    key_path = Path(private_key_path).resolve()
    if not key_path.is_file():
        raise RuntimeError("GitHub App private key file does not exist")
    transport = UrllibGitHubTransport()
    tokens = GitHubAppInstallationTokenProvider(
        client_id,
        key_path.read_text(encoding="utf-8"),
        transport,
    )
    return GitHubCatalog(tokens, transport)


github_catalog = _configured_github_catalog()
app = FastAPI(title="Forge API", version="0.1.0-dev")
allowed_web_origins = {
    "http://localhost:3000",
    "http://127.0.0.1:3000",
}
if configured_web_origin := os.environ.get("FORGE_WEB_ORIGIN"):
    allowed_web_origins.add(configured_web_origin)
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(allowed_web_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["authorization", "content-type"],
)


class RunBudgetRequest(BaseModel):
    total_tokens: int = Field(default=50_000, ge=1, le=1_000_000)
    cost_microusd: int = Field(default=1_000_000, ge=1, le=100_000_000)
    wall_seconds: float = Field(default=600, gt=0, le=86_400)
    model_steps: int = Field(default=30, ge=1, le=1_000)
    tool_calls: int = Field(default=80, ge=1, le=10_000)
    patch_attempts: int = Field(default=5, ge=1, le=100)

    def to_limits(self) -> BudgetLimits:
        return BudgetLimits(**self.model_dump())


class CreateRunRequest(BaseModel):
    repository_path: str = Field(min_length=1, max_length=4096)
    objective: str = Field(min_length=1, max_length=10_000)
    budgets: RunBudgetRequest = Field(default_factory=RunBudgetRequest)
    github_repository: "GitHubRepositoryRequest | None" = None

    @field_validator("objective")
    @classmethod
    def objective_must_contain_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("objective is required")
        return normalized


class GitHubRepositoryRequest(BaseModel):
    owner: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,100}$")
    name: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,100}$")
    installation_id: int = Field(gt=0)
    base_ref: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
    base_sha: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")

    def to_target(self) -> RepositoryTarget:
        return RepositoryTarget(**self.model_dump())


class GrantApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patch_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evaluation_verdict_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    approval_key: str = Field(
        min_length=16, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]+$"
    )
    expires_in_seconds: int = Field(default=900, ge=60, le=1_800)


class PublishRunRequest(BaseModel):
    approval_id: str = Field(min_length=1, max_length=128)
    patch_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    title: str = Field(min_length=1, max_length=256)
    body: str = Field(default="", max_length=20_000)
    idempotency_key: str = Field(
        min_length=16, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]+$"
    )


class CreateGitHubRunRequest(BaseModel):
    installation_id: int = Field(gt=0)
    owner: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,100}$")
    name: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,100}$")
    base_ref: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
    objective: str = Field(min_length=1, max_length=10_000)
    budgets: RunBudgetRequest = Field(default_factory=RunBudgetRequest)

    @field_validator("objective")
    @classmethod
    def objective_must_contain_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("objective is required")
        return normalized


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _benchmark_records() -> tuple[BenchmarkRecord, ...]:
    configured_path = os.environ.get("FORGE_BENCHMARK_RECORDS_PATH")
    if not configured_path:
        raise HTTPException(
            status_code=503, detail="benchmark evidence is not configured"
        )
    path = Path(configured_path).resolve()
    if not path.is_file():
        raise HTTPException(
            status_code=503, detail="configured benchmark evidence does not exist"
        )
    if path.stat().st_size > 10_000_000:
        raise HTTPException(status_code=413, detail="benchmark evidence is too large")
    try:
        with path.open("r", encoding="utf-8") as source:
            records = load_benchmark_jsonl(source)
    except (OSError, UnicodeError, ValueError) as error:
        raise HTTPException(
            status_code=503, detail=f"benchmark evidence is invalid: {error}"
        ) from error
    if not records:
        raise HTTPException(status_code=503, detail="benchmark evidence is empty")
    if len(records) > 1_000:
        raise HTTPException(
            status_code=413, detail="benchmark evidence exceeds 1000 records"
        )
    return records


@app.get("/benchmarks/latest")
def benchmark_dashboard(
    category: str | None = Query(
        default=None, pattern=r"^(?:frontend|backend|api|data|test)$"
    ),
    task_split: str | None = Query(
        default=None,
        alias="split",
        pattern=r"^(?:development|holdout|public)$",
    ),
) -> dict[str, object]:
    records = _benchmark_records()
    try:
        full_summary = summarize_benchmark(records)
    except ValueError as error:
        raise HTTPException(
            status_code=503, detail=f"benchmark evidence is inconsistent: {error}"
        ) from error
    selected = tuple(
        item
        for item in records
        if (category is None or item.category == category)
        and (task_split is None or item.task_split == task_split)
    )
    filtered_summary = summarize_benchmark(selected) if selected else None
    classification = os.environ.get(
        "FORGE_BENCHMARK_SOURCE_CLASSIFICATION", "development"
    ).strip().casefold()
    if classification not in {"synthetic", "development", "release"}:
        raise HTTPException(
            status_code=503,
            detail="benchmark source classification is invalid",
        )
    return {
        "source": {
            "classification": classification,
            "record_count": len(records),
        },
        "filters": {"category": category, "split": task_split},
        "available": {
            "categories": sorted({item.category for item in records}),
            "splits": sorted({str(item.task_split) for item in records}),
        },
        "summary": filtered_summary.to_dict() if filtered_summary else None,
        "summary_digest": filtered_summary.digest if filtered_summary else None,
        "full_summary": full_summary.to_dict(),
        "full_summary_digest": full_summary.digest,
        "records": [item.to_dict() for item in selected],
    }


def require_reviewer(request: Request) -> ReviewerPrincipal:
    try:
        return reviewer_authenticator.authenticate(request)
    except ReviewerAuthenticationUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except ReviewerAuthenticationError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    except ReviewerAuthorizationError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error


Reviewer = Annotated[ReviewerPrincipal, Depends(require_reviewer)]


@app.get("/auth/me")
def current_reviewer(reviewer: Reviewer) -> dict[str, str]:
    return reviewer.to_dict()


def _require_github_catalog() -> GitHubCatalog:
    if github_catalog is None:
        raise HTTPException(status_code=503, detail="GitHub App is not configured")
    return github_catalog


def _github_http_error(error: Exception) -> HTTPException:
    if isinstance(error, PublisherTransientError):
        return HTTPException(status_code=503, detail=str(error))
    if isinstance(error, PublisherPermissionError):
        return HTTPException(status_code=403, detail=str(error))
    return HTTPException(status_code=422, detail=str(error))


@app.get("/github/installations")
def list_github_installations(reviewer: Reviewer) -> list[dict[str, object]]:
    del reviewer
    try:
        return [item.to_dict() for item in _require_github_catalog().list_installations()]
    except (
        PublisherTransientError,
        PublisherPermissionError,
        PublisherConflictError,
    ) as error:
        raise _github_http_error(error) from error


@app.get("/github/installations/{installation_id}/repositories")
def list_github_repositories(
    installation_id: int, reviewer: Reviewer
) -> list[dict[str, object]]:
    del reviewer
    try:
        return [
            item.to_dict()
            for item in _require_github_catalog().list_repositories(installation_id)
        ]
    except (
        PublisherTransientError,
        PublisherPermissionError,
        PublisherConflictError,
        ValueError,
    ) as error:
        raise _github_http_error(error) from error


@app.post("/github/runs", status_code=201)
def create_github_run(
    request: CreateGitHubRunRequest, reviewer: Reviewer
) -> dict[str, object]:
    del reviewer
    catalog = _require_github_catalog()
    sandbox_id: str | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="forge-github-source-") as temporary:
            materialized = catalog.materialize_selected_repository(
                request.installation_id,
                request.owner,
                request.name,
                request.base_ref,
                Path(temporary) / "repository",
            )
            captured = controller.create(materialized.path)
            sandbox_id = captured.sandbox_id
            if captured.snapshot_artifact is None:
                raise RuntimeError("sandbox controller did not persist the source snapshot")
            source_snapshot = SourceSnapshot(**captured.snapshot_artifact.to_dict())
            controller.destroy(captured.sandbox_id)
            sandbox_id = None
        target = RepositoryTarget(
            materialized.owner,
            materialized.name,
            materialized.installation_id,
            materialized.base_ref,
            materialized.base_sha,
        )
        run = store.create_run(
            f"github://{materialized.owner}/{materialized.name}@{materialized.base_sha}",
            request.objective,
            request.budgets.to_limits(),
            target,
            source_snapshot,
        )
        store.append_event(
            str(run["run_id"]),
            "github_source_ingested",
            "github-gateway",
            {
                "repository": target.to_dict(),
                "tree_sha": materialized.tree_sha,
                "file_count": materialized.file_count,
                "total_bytes": materialized.total_bytes,
                "snapshot_artifact": source_snapshot.to_dict(),
            },
        )
        return store.get_run(str(run["run_id"]))
    except (
        PublisherTransientError,
        PublisherPermissionError,
        PublisherConflictError,
        ValueError,
    ) as error:
        raise _github_http_error(error) from error
    finally:
        if sandbox_id is not None:
            controller.destroy(sandbox_id)


@app.post("/runs", status_code=201)
def create_run(request: CreateRunRequest) -> dict[str, object]:
    path = Path(request.repository_path).expanduser().resolve()
    if not path.is_dir():
        raise HTTPException(status_code=422, detail="repository_path must be a directory")
    return store.create_run(
        str(path),
        request.objective,
        request.budgets.to_limits(),
        (
            request.github_repository.to_target()
            if request.github_repository is not None
            else None
        ),
    )


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, object]:
    try:
        return store.get_run(run_id)
    except RunNotFoundError as error:
        raise HTTPException(status_code=404, detail="run not found") from error


@app.get("/runs/{run_id}/events")
def list_events(
    run_id: str, after: int = Query(default=0, ge=0)
) -> list[dict[str, object]]:
    try:
        store.get_run(run_id)
    except RunNotFoundError as error:
        raise HTTPException(status_code=404, detail="run not found") from error
    return store.list_events(run_id, after_sequence=after)


@app.get("/runs/{run_id}/review")
def get_run_review(run_id: str) -> dict[str, object]:
    try:
        run = store.get_run(run_id)
    except RunNotFoundError as error:
        raise HTTPException(status_code=404, detail="run not found") from error
    patch_hash = run.get("evaluated_patch_hash")
    verdict_hash = run.get("evaluation_verdict_hash")
    if not isinstance(patch_hash, str) or not isinstance(verdict_hash, str):
        raise HTTPException(status_code=409, detail="run has no evaluated patch")

    started: dict[str, object] | None = None
    completed: dict[str, object] | None = None
    changed_paths: list[str] = []
    for event in store.list_events(run_id):
        payload = event["payload"]
        if event["event_type"] == "evaluation_started":
            started = payload
        elif event["event_type"] == "evaluation_completed":
            completed = payload
        elif (
            event["event_type"] == "state_changed"
            and payload.get("to_state") == "AWAITING_APPROVAL"
        ):
            value = payload.get("changed_paths")
            if isinstance(value, list) and all(isinstance(item, str) for item in value):
                changed_paths = value
    if started is None or completed is None:
        raise HTTPException(status_code=409, detail="evaluation evidence is incomplete")
    if completed.get("verdict_hash") != verdict_hash:
        raise HTTPException(status_code=409, detail="evaluation evidence does not match run")
    patch_payload = started.get("patch_artifact")
    if not isinstance(patch_payload, dict):
        raise HTTPException(status_code=409, detail="patch evidence is unavailable")
    try:
        patch_ref = ArtifactRef(
            sha256=str(patch_payload["sha256"]),
            size_bytes=int(patch_payload["size_bytes"]),
            media_type=str(patch_payload["media_type"]),
        )
        if patch_ref.sha256 != patch_hash:
            raise ValueError("patch hash mismatch")
        patch = controller.read_artifact(patch_ref, max_bytes=1_000_000).decode(
            "utf-8", errors="strict"
        )
    except (ArtifactNotFoundError, KeyError, TypeError, ValueError, UnicodeError) as error:
        raise HTTPException(
            status_code=409, detail="patch evidence failed integrity validation"
        ) from error

    return {
        "run_id": run_id,
        "patch_hash": patch_hash,
        "verdict_hash": verdict_hash,
        "verdict": completed.get("verdict"),
        "changed_paths": changed_paths,
        "checks": completed.get("checks", []),
        "rubric": completed.get("rubric", {}),
        "failure_codes": completed.get("failure_codes", []),
        "patch": patch,
        "repository": {
            "owner": run.get("repository_owner"),
            "name": run.get("repository_name"),
            "installation_id": run.get("installation_id"),
            "base_ref": run.get("base_ref"),
            "base_sha": run.get("base_sha"),
        },
    }


async def event_stream(run_id: str, after: int) -> AsyncIterator[str]:
    cursor = after
    idle_cycles = 0
    while idle_cycles < 120:
        events = store.list_events(run_id, after_sequence=cursor)
        if events:
            idle_cycles = 0
            for event in events:
                cursor = int(event["sequence"])
                yield f"data: {json.dumps(event, separators=(',', ':'))}\n\n"
        else:
            idle_cycles += 1
            yield ": keep-alive\n\n"
        await asyncio.sleep(0.5)


@app.get("/runs/{run_id}/events/stream")
def stream_events(
    run_id: str, after: int = Query(default=0, ge=0)
) -> StreamingResponse:
    try:
        store.get_run(run_id)
    except RunNotFoundError as error:
        raise HTTPException(status_code=404, detail="run not found") from error
    return StreamingResponse(
        event_stream(run_id, after), media_type="text/event-stream"
    )


@app.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str) -> dict[str, object]:
    try:
        return store.cancel_run(run_id)
    except RunNotFoundError as error:
        raise HTTPException(status_code=404, detail="run not found") from error
    except PublicationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/runs/{run_id}/approvals", status_code=201)
def grant_approval(
    run_id: str, request: GrantApprovalRequest, reviewer: Reviewer
) -> dict[str, object]:
    try:
        return store.grant_approval(
            run_id,
            request.patch_hash,
            request.evaluation_verdict_hash,
            reviewer.actor_id,
            request.approval_key,
            request.expires_in_seconds,
        )
    except RunNotFoundError as error:
        raise HTTPException(status_code=404, detail="run not found") from error
    except ApprovalExpiredError as error:
        raise HTTPException(status_code=410, detail=str(error)) from error
    except ApprovalError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/runs/{run_id}/publish", status_code=202)
def publish_run(
    run_id: str, request: PublishRunRequest, reviewer: Reviewer
) -> dict[str, object]:
    try:
        approval = store.get_approval(request.approval_id)
        if approval["run_id"] != run_id:
            raise ApprovalError("approval not found for run")
        if approval["actor_id"] != reviewer.actor_id:
            raise ReviewerAuthorizationError(
                "only the reviewer who approved this evidence may publish it"
            )
        return store.request_publication(
            run_id,
            request.approval_id,
            request.patch_hash,
            request.title,
            request.body,
            request.idempotency_key,
        )
    except RunNotFoundError as error:
        raise HTTPException(status_code=404, detail="run not found") from error
    except ApprovalExpiredError as error:
        raise HTTPException(status_code=410, detail=str(error)) from error
    except ReviewerAuthorizationError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except (ApprovalError, PublicationError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
