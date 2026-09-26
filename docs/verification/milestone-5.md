# Milestone 5 Independent Evaluation Verification

- Status: complete locally
- Date: 2026-09-26

## Implemented evidence

| Check | Result |
|---|---|
| Exact snapshot, snapshot-artifact, patch, and hidden-patch hash binding | Pass |
| Versioned task, fixture, benchmark, image, prompt, tool, model, policy, and grader manifest | Pass |
| Fresh sandbox reconstruction for evaluation | Pass |
| Hidden tests absent from solver snapshot | 5/5 private tasks pass |
| Hidden tests injected only after solver completion | Pass |
| Empty, invalid, oversized, forbidden-path, and protected-test patch policies | Pass |
| Bounded public, hidden, lint, type, and build check model | Pass |
| Separate deterministic rubric | Pass |
| Typed failure taxonomy | Pass |
| Content-addressed full evaluation report | Pass |
| Stable replay verdict hash | 5/5 private tasks pass |
| Answer patches accepted | 5/5 private tasks pass |
| Plausible incomplete patches rejected | 5/5 private tasks pass |
| Unchanged submissions rejected | 5/5 private tasks pass |
| Failed evaluation cannot reach approval | Pass |
| Passed evaluation reaches approval | Pass |
| Source benchmark checkout remains unchanged | Pass |

The local product suite collects 61 tests. Fifty-three pass. Five PostgreSQL
integration tests skip without a configured database, and three Docker abuse
tests skip without a configured immutable image.

The private benchmark ran two complementary proofs. First, all five stored
answer patches passed independent evaluation, all five deliberately incomplete
distractor patches failed hidden acceptance, all unchanged submissions failed,
and repeat evaluation produced the same verdict hash. Second, all five tasks
traversed the real durable worker path from bounded scripted agent execution to
`EVALUATING` and then `AWAITING_APPROVAL`.

The scripted agent proof uses private answer patches. It validates workflow and
evaluation behavior but does not claim that a live model discovered the fixes.

## Remaining external evidence

- Reproduce the new PostgreSQL migration and evaluator integration in public
  GitHub Actions.
- Run the Docker abuse lane with an immutable test image.
- Attempt the five-task benchmark with a pinned live model and publish task
  success, cost, latency, stop reasons, and diff metrics without revealing
  hidden tests or answers.
