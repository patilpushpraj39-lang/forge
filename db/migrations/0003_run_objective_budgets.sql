alter table runs
    add column objective text not null default
        'Inspect the repository and report verified findings.',
    add column budget_total_tokens bigint not null default 50000,
    add column budget_cost_microusd bigint not null default 1000000,
    add column budget_wall_seconds double precision not null default 600,
    add column budget_model_steps integer not null default 30,
    add column budget_tool_calls integer not null default 80,
    add column budget_patch_attempts integer not null default 5;

alter table runs
    add constraint runs_objective_length_check
        check (char_length(objective) between 1 and 10000),
    add constraint runs_budget_total_tokens_check
        check (budget_total_tokens between 1 and 1000000),
    add constraint runs_budget_cost_check
        check (budget_cost_microusd between 1 and 100000000),
    add constraint runs_budget_wall_check
        check (budget_wall_seconds > 0 and budget_wall_seconds <= 86400),
    add constraint runs_budget_model_steps_check
        check (budget_model_steps between 1 and 1000),
    add constraint runs_budget_tool_calls_check
        check (budget_tool_calls between 1 and 10000),
    add constraint runs_budget_patch_attempts_check
        check (budget_patch_attempts between 1 and 100);

alter table runs drop constraint runs_state_check;

alter table runs
    add constraint runs_state_check check (
        state in (
            'CREATED',
            'SNAPSHOTTING',
            'EXECUTING',
            'AWAITING_APPROVAL',
            'COMPLETED',
            'FAILED',
            'CANCELLED'
        )
    );
