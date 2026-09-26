from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from forge_agent_core import (
    ApprovalError,
    ApprovalExpiredError,
    BudgetLimits,
    PublicationError,
    RepositoryTarget,
    RunNotFoundError,
    create_run_store,
)
from forge_sandbox_controller import (
    ArtifactNotFoundError,
    ArtifactRef,
    create_sandbox_controller,
)


store = create_run_store()
controller = create_sandbox_controller()
app = FastAPI(title="Forge API", version="0.1.0-dev")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.environ.get("FORGE_WEB_ORIGIN", "http://localhost:3000")],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["content-type"],
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
    patch_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evaluation_verdict_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    actor_id: str = Field(min_length=1, max_length=128)
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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


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
    run_id: str, request: GrantApprovalRequest
) -> dict[str, object]:
    try:
        return store.grant_approval(
            run_id,
            request.patch_hash,
            request.evaluation_verdict_hash,
            request.actor_id,
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
    run_id: str, request: PublishRunRequest
) -> dict[str, object]:
    try:
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
    except (ApprovalError, PublicationError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
