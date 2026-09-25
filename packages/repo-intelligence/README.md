# Forge Repository Intelligence

This package builds immutable repository manifests and provides explainable,
bounded retrieval with snapshot and line provenance.

Current capabilities:

- root `.gitignore` and generated-directory filtering;
- language, build-system, test-command, symbol, and import detection;
- path, exact-text, symbol, dependency-neighborhood, and objective-ranked search;
- snapshot-integrity checks before content reads;
- binary and oversized file exclusion;
- character-budgeted, provenance-labelled context packs.

The package uses only the Python standard library. See the
[architecture record](../../docs/architecture/repository-intelligence.md) and
[Milestone 2 verification](../../docs/verification/milestone-2.md).
