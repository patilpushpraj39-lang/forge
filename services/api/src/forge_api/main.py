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

from forge_agent_core import BudgetLimits, RunNotFoundError, create_run_store


store = create_run_store()
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


@app.post("/runs", status_code=201)
def create_run(request: CreateRunRequest) -> dict[str, object]:
    path = Path(request.repository_path).expanduser().resolve()
    if not path.is_dir():
        raise HTTPException(status_code=422, detail="repository_path must be a directory")
    return store.create_run(
        str(path), request.objective, request.budgets.to_limits()
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
