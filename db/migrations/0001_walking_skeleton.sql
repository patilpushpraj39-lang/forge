create table if not exists runs (
    run_id uuid primary key,
    repository_path text not null,
    state text not null,
    attempt integer not null default 0,
    lease_owner text,
    lease_expires_at timestamptz,
    cancellation_requested_at timestamptz,
    row_version bigint not null default 0,
    created_at timestamptz not null default clock_timestamp(),
    updated_at timestamptz not null default clock_timestamp(),
    constraint runs_state_check check (
        state in (
            'CREATED',
            'SNAPSHOTTING',
            'EXECUTING',
            'COMPLETED',
            'FAILED',
            'CANCELLED'
        )
    ),
    constraint runs_attempt_check check (attempt >= 0),
    constraint runs_lease_pair_check check (
        (lease_owner is null and lease_expires_at is null)
        or (lease_owner is not null and lease_expires_at is not null)
    )
);

create table if not exists run_events (
    event_id uuid primary key,
    run_id uuid not null references runs(run_id) on delete cascade,
    sequence bigint not null,
    schema_version smallint not null default 1,
    event_type text not null,
    occurred_at timestamptz not null default clock_timestamp(),
    actor text not null,
    payload jsonb not null,
    constraint run_events_sequence_check check (sequence > 0),
    constraint run_events_schema_version_check check (schema_version = 1),
    constraint run_events_event_type_check check (
        event_type ~ '^[a-z][a-z0-9_]*$'
    ),
    constraint run_events_actor_check check (
        actor in (
            'user',
            'api',
            'worker',
            'model',
            'sandbox',
            'evaluator',
            'publisher'
        )
    ),
    constraint run_events_run_sequence_key unique (run_id, sequence)
);

create table if not exists outbox_messages (
    outbox_id bigint generated always as identity primary key,
    aggregate_id uuid not null references runs(run_id) on delete cascade,
    event_id uuid not null unique references run_events(event_id) on delete cascade,
    topic text not null,
    payload jsonb not null,
    created_at timestamptz not null default clock_timestamp(),
    published_at timestamptz
);

create index if not exists runs_created_claim_idx
    on runs (created_at, run_id)
    where state = 'CREATED' and cancellation_requested_at is null;

create index if not exists runs_expired_lease_claim_idx
    on runs (lease_expires_at, created_at, run_id)
    where state in ('SNAPSHOTTING', 'EXECUTING')
      and cancellation_requested_at is null;

create index if not exists runs_abandoned_cancellation_idx
    on runs (lease_expires_at, created_at, run_id)
    where state in ('SNAPSHOTTING', 'EXECUTING')
      and cancellation_requested_at is not null;

create index if not exists outbox_unpublished_idx
    on outbox_messages (outbox_id)
    where published_at is null;

create index if not exists outbox_aggregate_id_idx
    on outbox_messages (aggregate_id);
