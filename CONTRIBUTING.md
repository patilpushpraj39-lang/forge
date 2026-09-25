# Contributing to Forge

Forge is developed as a sequence of measurable vertical slices. Contributions should improve verified behavior, safety, reproducibility, failure recovery, reviewer comprehension, or measured cost and latency.

## Before opening a change

- Link the change to a milestone and an issue with explicit acceptance checks.
- Add an architecture decision record only for a durable, expensive-to-reverse choice.
- Identify new trust boundaries, secrets, external side effects, and resource risks.
- Define the event, trace, or metric needed to diagnose failures.

## Pull request expectations

- Keep changes coherent and reviewable.
- Add positive and negative tests for new behavior.
- Keep contracts, migrations, and documentation with the implementation.
- Make retries safe when a step can be delivered more than once.
- Do not include hidden benchmark tests or answer patches in this repository.
- Never report planned performance as measured performance.

Run the local scaffold check before submitting:

```bash
node scripts/validate-scaffold.mjs
```

