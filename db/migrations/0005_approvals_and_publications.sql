alter table runs
    add column repository_owner text,
    add column repository_name text,
    add column installation_id bigint,
    add column base_ref text,
    add column base_sha text,
    add column evaluated_patch_hash text,
    add column evaluation_verdict_hash text;

alter table runs
    add constraint runs_repository_target_check check (
        (
            repository_owner is null
            and repository_name is null
            and installation_id is null
            and base_ref is null
            and base_sha is null
        )
        or (
            repository_owner ~ '^[A-Za-z0-9_.-]{1,100}$'
            and repository_name ~ '^[A-Za-z0-9_.-]{1,100}$'
            and installation_id > 0
            and base_ref ~ '^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$'
            and base_sha ~ '^([0-9a-f]{40}|[0-9a-f]{64})$'
        )
    ),
    add constraint runs_evaluation_hash_pair_check check (
        (evaluated_patch_hash is null and evaluation_verdict_hash is null)
        or (
            evaluated_patch_hash ~ '^[0-9a-f]{64}$'
            and evaluation_verdict_hash ~ '^[0-9a-f]{64}$'
        )
    );

alter table runs drop constraint runs_state_check;

alter table runs
    add constraint runs_state_check check (
        state in (
            'CREATED',
            'SNAPSHOTTING',
            'EXECUTING',
            'EVALUATING',
            'AWAITING_APPROVAL',
            'PUBLISHING',
            'COMPLETED',
            'FAILED',
            'CANCELLED'
        )
    );

create table approvals (
    approval_id uuid primary key,
    approval_key text not null unique,
    run_id uuid not null references runs(run_id) on delete cascade,
    patch_hash text not null check (patch_hash ~ '^[0-9a-f]{64}$'),
    evaluation_verdict_hash text not null
        check (evaluation_verdict_hash ~ '^[0-9a-f]{64}$'),
    repository_owner text not null,
    repository_name text not null,
    installation_id bigint not null check (installation_id > 0),
    base_ref text not null,
    base_sha text not null check (base_sha ~ '^([0-9a-f]{40}|[0-9a-f]{64})$'),
    action text not null check (action = 'create_pull_request'),
    actor_id text not null,
    expires_at timestamptz not null,
    created_at timestamptz not null default clock_timestamp()
);

create index approvals_run_created_idx
    on approvals (run_id, created_at desc);

create table publication_jobs (
    publication_id uuid primary key,
    idempotency_key text not null unique,
    run_id uuid not null unique references runs(run_id) on delete cascade,
    approval_id uuid not null references approvals(approval_id),
    patch_hash text not null check (patch_hash ~ '^[0-9a-f]{64}$'),
    repository_owner text not null,
    repository_name text not null,
    installation_id bigint not null check (installation_id > 0),
    base_ref text not null,
    base_sha text not null check (base_sha ~ '^([0-9a-f]{40}|[0-9a-f]{64})$'),
    branch_name text not null,
    pull_request_title text not null,
    pull_request_body text not null,
    status text not null check (
        status in ('PENDING', 'CLAIMED', 'COMPLETED', 'FAILED')
    ),
    attempt integer not null default 0 check (attempt >= 0),
    available_at timestamptz not null default clock_timestamp(),
    lease_owner text,
    lease_expires_at timestamptz,
    github_pull_request_number integer,
    github_pull_request_url text,
    github_head_sha text,
    failure_code text,
    created_at timestamptz not null default clock_timestamp(),
    updated_at timestamptz not null default clock_timestamp(),
    constraint publication_lease_pair_check check (
        (lease_owner is null and lease_expires_at is null)
        or (lease_owner is not null and lease_expires_at is not null)
    )
);

create index publication_jobs_claim_idx
    on publication_jobs (
        status,
        available_at,
        lease_expires_at,
        created_at,
        publication_id
    )
    where status in ('PENDING', 'CLAIMED');
