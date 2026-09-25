# Repository Intelligence Architecture

## Purpose

Repository intelligence turns an immutable sandbox snapshot into bounded,
traceable context. It helps Forge find likely fault locations without sending an
entire repository to a model or trusting mutable host paths.

## Flow

```text
sandbox snapshot
  -> ignore-aware manifest
  -> language, build, symbol, and import analysis
  -> path, exact-text, symbol, or objective-ranked retrieval
  -> dependency-neighborhood expansion
  -> character-budgeted context pack
  -> snapshot + path + line provenance on every snippet
```

The worker receives only an opaque sandbox ID. Indexing and content retrieval
remain operations on the sandbox-controller boundary, so the worker never gains
the disposable workspace path.

## Manifest

The version-one manifest records:

- snapshot and manifest hashes;
- normalized repository-relative paths, byte sizes, and content hashes;
- language and line counts;
- extracted Python and TypeScript/JavaScript symbols and imports;
- resolved in-repository dependency edges;
- detected build systems and suggested test commands;
- ignored paths and explicit binary or oversized exclusions.

The manifest is deterministic for a given snapshot. Retrieval rechecks each
file's content hash and fails if content changed after indexing.

## Retrieval

The first retrieval layer intentionally uses explainable structural and lexical
signals instead of embeddings:

- path substring or glob search;
- case-insensitive exact text search;
- Python and TypeScript/JavaScript symbol search;
- incoming and outgoing import-neighborhood traversal;
- objective-to-file ranking using path, symbol, and source-token overlap.

Test files are eligible evidence but receive a ranking penalty so matching
production code is preferred. Embeddings remain deferred until benchmark
failures demonstrate that lexical and structural retrieval are insufficient.

## Context safety

- Common generated directories and root `.gitignore` rules are excluded.
- Symlinks are never followed.
- Binary and oversized files stay visible as manifest metadata but cannot enter
  retrieval or context packs.
- File-count and per-file byte limits are explicit.
- Context packs have a hard character budget and deduplicate provenance ranges.
- Every context item is addressed by snapshot hash, path, and line range.

## Current limitation

Version one reads the root `.gitignore` and covers its common pattern forms. A
future hardening pass can add nested ignore files and full Git wildmatch parity
if real repositories require them.
