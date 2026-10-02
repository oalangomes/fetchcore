# ADR-0004 — Versioned persisted identities and fail-closed compatibility

## Status

Accepted.

## Context

Incremental indexing and replay reuse persisted projections, snapshots, vectors, and sparse signals. Their meaning depends on projection layout, serializer, index/schema, embedding metadata, analyzer, and source/extractor fingerprints. Physical module moves must not silently change those meanings.

## Decision

Keep persisted identities explicit and validate compatibility before reuse. An incompatible schema or fingerprint fails closed, requiring a deliberate rebuild or migration. Retain historical serialized IDs when only code ownership moves.

## Consequences

This prevents stale vectors or snapshots from silently contaminating results and makes reproduction possible. Operators may have to rebuild; identity changes need versioning, compatibility tests, and clear migration instructions.

## Alternatives considered

Best-effort compatibility, silent fallback, or unversioned state would hide semantic drift. Automatic rebuild without an explicit reason could discard a reproducible state or cause unexpected cost.

## Related issues/PRs

[Issue #44 equivalence](../migrations/issue-44-equivalence.md), [namespace migration](../migrations/fetchcore-namespace.md), [projection](../architecture/projection.md), `src/fetchcore/projection/core.py`, `src/fetchcore/repository/index_fingerprint.py`.
