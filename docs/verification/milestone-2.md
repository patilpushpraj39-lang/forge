# Milestone 2 Repository Intelligence Verification

- Status: implementation complete; reproduced in GitHub CI
- Date: 2026-09-26

Update on 2026-10-02: the current repository-intelligence suite is included in
the passing `verify` job of
[CI run 12](https://github.com/patilpushpraj39-lang/forge/actions/runs/36991989591)
at commit `1e8d038`. The local evidence below records the original milestone
checks; this CI run provides the subsequent clean-environment reproduction.

## Delivered

- Ignore-aware, snapshot-bound repository manifest.
- Language and build-system detection with suggested verification commands.
- Python and TypeScript/JavaScript symbol and import extraction.
- Path, exact-text, symbol, objective-ranked, and dependency-neighborhood search.
- Character-budgeted context packs with snapshot, path, and line provenance.
- Binary, oversized, symlink, file-count, and snapshot-integrity protections.
- Bounded sandbox-controller indexing and retrieval operations.
- Repository brief shown in the web run console.

## Passing evidence

| Check | Result |
|---|---|
| Repository-intelligence unit tests | 5 passed |
| Existing walking-skeleton regression tests | 8 passed |
| PostgreSQL integration definitions | 4 collected; skipped locally because PostgreSQL is unavailable |
| Web TypeScript check | Passed |
| Web production build | Passed |
| Private seed-task retrieval | 5 of 5 fault-source files found in the top three results |
| Required seed-task threshold | Passed; requirement is at least 4 of 5 |

The seed retrieval check read only each public issue statement and base fixture.
It did not read hidden tests or answer patches. All five expected production
source files appeared within the first three objective-ranked results.

## Safety assertions

- Every search result contains the immutable snapshot hash, repository-relative
  path, start line, and end line.
- Context construction rejects results from a different snapshot.
- Retrieval detects content changed after indexing.
- Binary and oversized fixture content cannot enter search results or context.
- A deliberately undersized context budget returns a truncated pack without
  exceeding the limit.

## Remaining external check

Run the complete suite in GitHub Actions so the updated nine-event PostgreSQL
and live SSE paths are reproduced in a clean Linux environment.
