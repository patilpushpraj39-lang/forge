from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from forge_agent_core.run_store import RunNotFoundError, RunStore


def database_path() -> Path:
    configured = os.environ.get("FORGE_DATABASE_PATH")
    return Path(configured) if configured else Path(".state/forge.db")


store = RunStore(database_path())
app = FastAPI(title="Forge API", version="0.1.0-dev")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.environ.get("FORGE_WEB_ORIGIN", "http://localhost:3000")],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["content-type"],
)


class CreateRunRequest(BaseModel):
    repository_path: str = Field(min_length=1, max_length=4096)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/runs", status_code=201)
def create_run(request: CreateRunRequest) -> dict[str, object]:
    path = Path(request.repository_path).expanduser().resolve()
    if not path.is_dir():
        raise HTTPException(status_code=422, detail="repository_path must be a directory")
    return store.create_run(str(path))


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

