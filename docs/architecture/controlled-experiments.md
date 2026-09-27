# Controlled Experiments

Forge's first live-model measurements use a private development split and a
frozen experiment contract. The contract prevents a result from silently
changing because the product code, benchmark tasks, model settings, budgets,
or pricing snapshot changed between runs.

## Frozen baseline

The development baseline selects ten private tasks and pins:

- the full product and private benchmark Git revisions;
- the benchmark-content and individual task-manifest digests;
- `gpt-6-sol` with medium reasoning, the default service tier, and response
  storage disabled;
- the prompt and tool contract versions;
- token, cost, wall-time, model-step, tool-call, and patch-attempt ceilings;
- the local development sandbox image digest; and
- the pricing values and effective date used for cost accounting.

The default service tier is requested explicitly so the experiment does not
inherit an account-level latency or pricing preference. Model and pricing
values were checked against the official [model documentation](https://developers.openai.com/api/docs/models),
[Responses API reference](https://developers.openai.com/api/reference/cli/resources/responses/methods/create),
and [pricing page](https://developers.openai.com/api/docs/pricing) when the
contract was created.

## One-variable comparisons

Two development experiments are defined. One lowers the tool-call ceiling from
20 to 12. The other lowers the patch-attempt ceiling from three to one. A
machine-checked matrix rejects either experiment if any other configuration
field differs from the baseline.

Each configuration selects the same ten development tasks: one API, three
backend, two data, two frontend, and two test-infrastructure tasks. At the
configured per-task ceiling, each ten-task run has a maximum recorded model
cost of 20,000,000 microdollars ($20). This is a safety ceiling, not an expected
cost or a spend authorization.

## Evidence boundary

The current lock files record and verify the exact product and private
benchmark revisions. They are local run inputs and are not published because
they contain private task identifiers and manifest hashes.

No live-model run has been executed yet. Planning, freezing, deterministic
answer-patch orchestration, or synthetic dashboard records are not autonomous
model-performance evidence. Development-set results will remain labeled
development evidence; only the independent holdout path running in the Docker
sandbox can become release evidence after all reporting gates pass.

## Execution order

1. Verify the frozen baseline lock and required credentials.
2. Execute every selected baseline task once and export every outcome.
3. Diagnose infrastructure invalidations before interpreting solver failures.
4. Execute the two one-variable experiments against the same task population.
5. Select a configuration from development evidence without reading holdout
   outcomes.
6. Tag the product revision, run the untouched holdout set in Docker, and
   reproduce the report from saved records.

If a source revision or contract field changes, the existing lock becomes
invalid. A new lock and a new experiment identifier are required; previous and
new results must not be aggregated as one configuration.
