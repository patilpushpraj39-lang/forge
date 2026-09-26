# Independent Patch Evaluation

## Purpose

Forge does not trust an agent's statement that its own patch works. Evaluation
is a separate worker stage with trusted inputs, a clean sandbox, explicit
policies, and a deterministic verdict. Human approval is possible only after
this stage passes.

## Trust boundary

The solver receives the immutable repository snapshot, bounded tools, and
public repository checks. It cannot read hidden tests. After the solver stops,
the worker gives the evaluator content-addressed references to the original
snapshot and exact generated patch. Hidden checks are injected only into the
fresh evaluation sandbox.

```text
immutable snapshot + stored patch + versioned manifest
                         |
                         v
                 static patch policy
                         |
                         v
               fresh snapshot replay
                         |
                         v
               trusted public checks
                         |
                         v
              inject hidden-test patch
                         |
                         v
               trusted hidden checks
                         |
                         v
                independent rubric
                         |
            +------------+------------+
            |                         |
          passed                    failed
            |                         |
            v                         v
    AWAITING_APPROVAL               FAILED
```

## Manifest and reproducibility

Every evaluation manifest binds the repository content hash, snapshot artifact
hash, solution patch hash, optional hidden-test artifact hash, task and fixture
versions, benchmark version, immutable sandbox image digest, prompt version,
tool version, model version, policy version, grader version, and exact check
commands. This prevents a result from being silently attributed to different
inputs.

The complete manifest and full report are stored as content-addressed
artifacts. The evaluation-started event also records the snapshot, patch, and
optional hidden-patch artifact references needed for replay. A second stable
verdict hash excludes raw command output and other incidental text, allowing
the same versioned inputs to reproduce the same decision.

## Patch policy

The evaluator parses the unified diff before execution. It rejects empty or
invalid patches, path escapes, forbidden configuration or credential paths,
protected-test changes, excessive file counts, and excessive additions,
deletions, or total bytes. A hidden-test patch has a narrower contract: it may
only add or modify explicitly hidden test files and may not delete files.

## Checks, rubric, and failures

Checks are typed as setup, build, lint, typecheck, public test, or hidden test.
Each has a bounded timeout and output limit. Required check failures produce a
typed failure code. The deterministic rubric is a separate component and
scores policy compliance, required checks, and patch scope. Reports preserve
both the check evidence and a compact failure taxonomy.

## Durable workflow

`EVALUATING` is an active leased state. Its lease can be renewed, cancelled,
or recovered after worker loss in the same way as snapshotting and agent
execution. A passed verdict transitions to `AWAITING_APPROVAL`; every failed
verdict transitions to `FAILED`. The generated source checkout is never
modified because evaluation always uses a disposable reconstruction.
