from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from forge_agent_core import (
    ApprovalError,
    ApprovalExpiredError,
    BudgetLimits,
    PublicationError,
    RepositoryTarget,
    RunNotFoundError,
    RunStoreProtocol,
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
    SandboxController,
    create_sandbox_controller,
)
from forge_worker import OFFLINE_DEMO_OBJECTIVE, execute_offline_demo

from .auth import (
    ReviewerAuthenticator,
    ReviewerAuthorizationError,
    ReviewerPrincipal,
    create_reviewer_authenticator,
)
from .access import (
    authenticate_reviewer,
    create_api_application,
    validate_api_profile,
)
from .readonly_preview import build_preview, receipt
from forge_agent_core.readonly_review import EFFECTS, SCOPE as READONLY_REVIEW_SCOPE


api_profile = validate_api_profile()
reviewer_authenticator: ReviewerAuthenticator = create_reviewer_authenticator()
store = create_run_store()
controller = create_sandbox_controller()


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
app = create_api_application(api_profile, reviewer_authenticator)


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


class OfflineDemoDecisionRequest(BaseModel):
    """A record-only decision that can never authorize an external write."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["approved", "rejected"]
    patch_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evaluation_verdict_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision_key: str = Field(
        min_length=16, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]+$"
    )


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
    return authenticate_reviewer(request, reviewer_authenticator)


Reviewer = Annotated[ReviewerPrincipal, Depends(require_reviewer)]


@app.get("/auth/me")
def current_reviewer(reviewer: Reviewer) -> dict[str, str]:
    return reviewer.to_dict()


class ReadonlyPreviewDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["readonly-preview-record-only-v1"]
    repository: str = Field(min_length=3, max_length=201, pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["approved", "rejected"]
    decision_key: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    acknowledge_record_only: StrictBool

    @field_validator("acknowledge_record_only")
    @classmethod
    def record_only_acknowledged(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Acknowledge record-only review; execution remains disabled.")
        return value


def _readonly_preview(run_id: str, repository: str) -> dict:
    try:
        return build_preview(store, controller, run_id, repository)
    except RunNotFoundError as error:
        raise HTTPException(status_code=404, detail="Source reference not found") from error
    except Exception as error:
        # Never return raw artifact paths, repository bytes or provider errors.
        raise HTTPException(status_code=409, detail="Source preview unavailable or ineligible") from error


@app.get("/readonly-previews/{run_id}")
def get_readonly_preview(run_id: str, reviewer: Reviewer, response: Response, repository: str = Query(min_length=3, max_length=201)) -> dict:
    del reviewer
    response.headers["Cache-Control"] = "private, no-store"
    return _readonly_preview(run_id, repository)


@app.post("/readonly-previews/{run_id}/decision")
def decide_readonly_preview(run_id: str, request: ReadonlyPreviewDecisionRequest, reviewer: Reviewer, response: Response) -> dict:
    response.headers["Cache-Control"] = "private, no-store"
    preview = _readonly_preview(run_id, request.repository)
    plan = preview["plan"]
    if request.plan_sha256 != plan["plan_sha256"]:
        raise HTTPException(status_code=409, detail="Preview changed; reload and review again")
    payload = {"scope": READONLY_REVIEW_SCOPE, "decision": request.decision,
               "plan_sha256": plan["plan_sha256"], "repository": plan["repository"],
               "base_sha": plan["base_sha"], "snapshot_sha256": plan["snapshot_sha256"],
               "decision_key_sha256": hashlib.sha256(request.decision_key.encode("ascii")).hexdigest(), **EFFECTS}
    try:
        event = store.record_readonly_preview_decision(run_id, reviewer.actor_id, payload)
    except RunNotFoundError as error:
        raise HTTPException(status_code=404, detail="Source reference not found") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail="Review conflicts with the stored source or decision") from error
    return receipt(event)


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


def _offline_demo_fixture_path() -> Path:
    configured = os.environ.get("FORGE_OFFLINE_DEMO_FIXTURE_PATH")
    if configured:
        path = Path(configured).expanduser().resolve()
    else:
        repository_root = Path(__file__).resolve().parents[4]
        path = repository_root / "evals" / "public-tasks" / "status-normalizer"
    if not path.is_dir():
        raise RuntimeError("offline demo fixture is unavailable")
    return path


@app.post("/demo/runs", status_code=201)
def create_offline_demo_run() -> dict[str, object]:
    """Execute the public deterministic scenario through the real local workflow."""
    try:
        created = store.create_run(
            str(_offline_demo_fixture_path()),
            OFFLINE_DEMO_OBJECTIVE,
            BudgetLimits(
                total_tokens=1,
                cost_microusd=1,
                wall_seconds=60,
                model_steps=10,
                tool_calls=20,
                patch_attempts=2,
            ),
        )
        run_id = str(created["run_id"])
        store.append_event(
            run_id,
            "offline_demo_configured",
            "api",
            {
                "scenario": "status-normalizer-public-smoke",
                "runtime": "forge-deterministic-demo-v1",
                "provider_calls_enabled": False,
                "github_writes_enabled": False,
            },
        )
        execute_offline_demo(store, run_id, controller)
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"offline demo failed: {type(error).__name__}",
        ) from error

    return _build_offline_demo_response(run_id)


def _offline_demo_decision(
    events: list[dict[str, object]],
) -> dict[str, object] | None:
    recorded = next(
        (
            event
            for event in reversed(events)
            if event["event_type"] == "offline_demo_review_decided"
        ),
        None,
    )
    if recorded is None:
        return None
    payload = recorded["payload"]
    if not isinstance(payload, dict):
        raise HTTPException(status_code=409, detail="demo review evidence is invalid")
    return {
        "event_id": recorded["event_id"],
        "decision": payload.get("decision"),
        "decision_key": payload.get("decision_key"),
        "actor": recorded["actor"],
        "recorded_at": recorded["occurred_at"],
        "patch_hash": payload.get("patch_hash"),
        "verdict_hash": payload.get("verdict_hash"),
        "authorizes_github_write": False,
    }


def _build_offline_demo_response(run_id: str) -> dict[str, object]:
    try:
        run = store.get_run(run_id)
    except RunNotFoundError as error:
        raise HTTPException(status_code=404, detail="demo run not found") from error
    events = store.list_events(run_id)
    configured = next(
        (event for event in events if event["event_type"] == "offline_demo_configured"),
        None,
    )
    if configured is None:
        raise HTTPException(status_code=404, detail="demo run not found")
    review = _build_run_review(run_id, store, controller)

    agent_stopped = next(
        (
            event["payload"]
            for event in events
            if event["event_type"] == "agent_stopped"
        ),
        {},
    )
    return {
        "mode": "local_deterministic",
        "run": run,
        "events": events,
        "review": review,
        "decision": _offline_demo_decision(events),
        "safety": {
            "provider": "offline",
            "model": "forge-deterministic-demo-v1",
            "model_calls": 0,
            "network_requests": 0,
            "cost_microusd": int(agent_stopped.get("cost_microusd", 0)),
            "github_writes": 0,
        },
    }


@app.get("/demo/runs/{run_id}")
def get_offline_demo_run(run_id: str) -> dict[str, object]:
    """Restore one durable offline demo and its record-only review decision."""

    return _build_offline_demo_response(run_id)


@app.post("/demo/runs/{run_id}/decision")
def record_offline_demo_decision(
    run_id: str, request: OfflineDemoDecisionRequest
) -> dict[str, object]:
    """Record a local tour decision without creating publication authority."""

    response = _build_offline_demo_response(run_id)
    run = response["run"]
    review = response["review"]
    if not isinstance(run, dict) or run.get("state") != "AWAITING_APPROVAL":
        raise HTTPException(status_code=409, detail="demo is not awaiting review")
    if not isinstance(review, dict):
        raise HTTPException(status_code=409, detail="demo review evidence is unavailable")
    if (
        request.patch_hash != review.get("patch_hash")
        or request.evaluation_verdict_hash != review.get("verdict_hash")
    ):
        raise HTTPException(
            status_code=409,
            detail="decision does not match the evaluated patch and verdict",
        )

    existing = response.get("decision")
    if isinstance(existing, dict):
        same_request = (
            existing.get("decision") == request.decision
            and existing.get("decision_key") == request.decision_key
            and existing.get("patch_hash") == request.patch_hash
            and existing.get("verdict_hash") == request.evaluation_verdict_hash
        )
        if same_request:
            return response
        raise HTTPException(status_code=409, detail="demo review decision is already recorded")

    store.append_event(
        run_id,
        "offline_demo_review_decided",
        "local:demo-reviewer",
        {
            "decision": request.decision,
            "decision_key": request.decision_key,
            "patch_hash": request.patch_hash,
            "verdict_hash": request.evaluation_verdict_hash,
            "effect": "record_only",
            "github_write_authorized": False,
        },
    )
    return _build_offline_demo_response(run_id)


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
    return _build_run_review(run_id, store, controller)


def _build_run_review(
    run_id: str,
    selected_store: RunStoreProtocol,
    selected_controller: SandboxController,
) -> dict[str, object]:
    try:
        run = selected_store.get_run(run_id)
    except RunNotFoundError as error:
        raise HTTPException(status_code=404, detail="run not found") from error
    patch_hash = run.get("evaluated_patch_hash")
    verdict_hash = run.get("evaluation_verdict_hash")
    if not isinstance(patch_hash, str) or not isinstance(verdict_hash, str):
        raise HTTPException(status_code=409, detail="run has no evaluated patch")

    started: dict[str, object] | None = None
    completed: dict[str, object] | None = None
    changed_paths: list[str] = []
    for event in selected_store.list_events(run_id):
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
        patch = selected_controller.read_artifact(
            patch_ref, max_bytes=1_000_000
        ).decode(
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
