from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .model_runtime import (
    ModelStepResult,
    StopReason,
    TokenUsage,
    ToolCall,
    ToolOutput,
)


class IdempotencyRecordTypeError(RuntimeError):
    pass


class SqliteIdempotencyLedger:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_idempotency_records (
                    idempotency_key TEXT PRIMARY KEY,
                    record_kind TEXT NOT NULL CHECK (
                        record_kind IN ('model_step', 'tool_output')
                    ),
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.commit()

    def get_model_step(self, key: str) -> ModelStepResult | None:
        payload = self._get(key, "model_step")
        return None if payload is None else _model_step_from_dict(payload)

    def put_model_step(self, key: str, result: ModelStepResult) -> None:
        self._put(key, "model_step", _model_step_to_dict(result))

    def get_tool_output(self, key: str) -> ToolOutput | None:
        payload = self._get(key, "tool_output")
        return None if payload is None else _tool_output_from_dict(payload)

    def put_tool_output(self, key: str, output: ToolOutput) -> None:
        self._put(key, "tool_output", _tool_output_to_dict(output))

    def _get(self, key: str, expected_kind: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT record_kind, payload_json
                FROM agent_idempotency_records
                WHERE idempotency_key = ?
                """,
                (key,),
            ).fetchone()
        if row is None:
            return None
        if row[0] != expected_kind:
            raise IdempotencyRecordTypeError(
                f"expected {expected_kind}, found {row[0]}"
            )
        return json.loads(row[1])

    def _put(
        self, key: str, kind: str, payload: dict[str, Any]
    ) -> None:
        rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT OR IGNORE INTO agent_idempotency_records(
                    idempotency_key, record_kind, payload_json
                ) VALUES (?, ?, ?)
                """,
                (key, kind, rendered),
            )
            row = connection.execute(
                """
                SELECT record_kind, payload_json
                FROM agent_idempotency_records
                WHERE idempotency_key = ?
                """,
                (key,),
            ).fetchone()
            connection.commit()
        if row is None or row[0] != kind or row[1] != rendered:
            raise IdempotencyRecordTypeError(
                "idempotency key already contains a different result"
            )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=10)
        try:
            yield connection
        finally:
            connection.close()


class PostgresIdempotencyLedger:
    def __init__(
        self,
        database_url: str,
        migrations_path: str | Path,
        *,
        min_pool_size: int = 1,
        max_pool_size: int = 4,
    ) -> None:
        from psycopg_pool import ConnectionPool

        from .postgres_run_store import apply_migrations

        apply_migrations(database_url, migrations_path)
        self.pool = ConnectionPool(
            conninfo=database_url,
            min_size=min_pool_size,
            max_size=max_pool_size,
            open=True,
            kwargs={"autocommit": False},
        )
        self.pool.wait(timeout=10)

    def close(self) -> None:
        self.pool.close()

    def get_model_step(self, key: str) -> ModelStepResult | None:
        payload = self._get(key, "model_step")
        return None if payload is None else _model_step_from_dict(payload)

    def put_model_step(self, key: str, result: ModelStepResult) -> None:
        self._put(key, "model_step", _model_step_to_dict(result))

    def get_tool_output(self, key: str) -> ToolOutput | None:
        payload = self._get(key, "tool_output")
        return None if payload is None else _tool_output_from_dict(payload)

    def put_tool_output(self, key: str, output: ToolOutput) -> None:
        self._put(key, "tool_output", _tool_output_to_dict(output))

    def _get(self, key: str, expected_kind: str) -> dict[str, Any] | None:
        with self.pool.connection() as connection:
            with connection.transaction():
                row = connection.execute(
                    """
                    SELECT record_kind, payload_json
                    FROM agent_idempotency_records
                    WHERE idempotency_key = %s
                    """,
                    (key,),
                ).fetchone()
        if row is None:
            return None
        if row[0] != expected_kind:
            raise IdempotencyRecordTypeError(
                f"expected {expected_kind}, found {row[0]}"
            )
        return dict(row[1])

    def _put(
        self, key: str, kind: str, payload: dict[str, Any]
    ) -> None:
        with self.pool.connection() as connection:
            with connection.transaction():
                row = connection.execute(
                    """
                    INSERT INTO agent_idempotency_records(
                        idempotency_key, record_kind, payload_json
                    ) VALUES (%s, %s, %s::jsonb)
                    ON CONFLICT (idempotency_key) DO UPDATE
                    SET idempotency_key = EXCLUDED.idempotency_key
                    RETURNING record_kind, payload_json
                    """,
                    (
                        key,
                        kind,
                        json.dumps(
                            payload, sort_keys=True, separators=(",", ":")
                        ),
                    ),
                ).fetchone()
        if row is None or row[0] != kind or dict(row[1]) != payload:
            raise IdempotencyRecordTypeError(
                "idempotency key already contains a different result"
            )


def _model_step_to_dict(result: ModelStepResult) -> dict[str, Any]:
    return {
        "provider": result.provider,
        "model": result.model,
        "response_id": result.response_id,
        "stop_reason": result.stop_reason,
        "usage": {
            "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens,
            "cached_input_tokens": result.usage.cached_input_tokens,
            "reasoning_tokens": result.usage.reasoning_tokens,
        },
        "cost_microusd": result.cost_microusd,
        "prompt_version": result.prompt_version,
        "tool_version": result.tool_version,
        "tool_calls": [
            {
                "call_id": call.call_id,
                "name": call.name,
                "arguments": call.arguments,
            }
            for call in result.tool_calls
        ],
        "structured_output": result.structured_output,
        "output_text": result.output_text,
        "trace_references": list(result.trace_references),
    }


def _model_step_from_dict(payload: dict[str, Any]) -> ModelStepResult:
    usage = payload["usage"]
    return ModelStepResult(
        provider=payload["provider"],
        model=payload["model"],
        response_id=payload["response_id"],
        stop_reason=StopReason(payload["stop_reason"]),
        usage=TokenUsage(
            usage["input_tokens"],
            usage["output_tokens"],
            usage["cached_input_tokens"],
            usage["reasoning_tokens"],
        ),
        cost_microusd=payload["cost_microusd"],
        prompt_version=payload["prompt_version"],
        tool_version=payload["tool_version"],
        tool_calls=tuple(
            ToolCall(item["call_id"], item["name"], item["arguments"])
            for item in payload["tool_calls"]
        ),
        structured_output=payload["structured_output"],
        output_text=payload["output_text"],
        trace_references=tuple(payload["trace_references"]),
    )


def _tool_output_to_dict(output: ToolOutput) -> dict[str, Any]:
    return {"call_id": output.call_id, "output": output.output}


def _tool_output_from_dict(payload: dict[str, Any]) -> ToolOutput:
    return ToolOutput(payload["call_id"], payload["output"])
