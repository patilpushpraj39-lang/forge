alter table runs drop constraint runs_state_check;

alter table runs
    add constraint runs_state_check check (
        state in (
            'CREATED',
            'SNAPSHOTTING',
            'EXECUTING',
            'EVALUATING',
            'AWAITING_APPROVAL',
            'COMPLETED',
            'FAILED',
            'CANCELLED'
        )
    );

drop index if exists runs_expired_lease_claim_idx;
create index runs_expired_lease_claim_idx
    on runs (lease_expires_at, created_at, run_id)
    where state in ('SNAPSHOTTING', 'EXECUTING', 'EVALUATING')
      and cancellation_requested_at is null;

drop index if exists runs_abandoned_cancellation_idx;
create index runs_abandoned_cancellation_idx
    on runs (lease_expires_at, created_at, run_id)
    where state in ('SNAPSHOTTING', 'EXECUTING', 'EVALUATING')
      and cancellation_requested_at is not null;
