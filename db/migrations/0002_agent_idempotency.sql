CREATE TABLE agent_idempotency_records (
    idempotency_key TEXT PRIMARY KEY,
    record_kind TEXT NOT NULL CHECK (record_kind IN ('model_step', 'tool_output')),
    payload_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX agent_idempotency_records_created_at_idx
    ON agent_idempotency_records(created_at);
