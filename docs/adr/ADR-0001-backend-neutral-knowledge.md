# ADR-0001 — Backend-neutral canonical Repository Knowledge and rebuildable projections

## Status

Accepted.

## Context

FetchCore indexes repository sources into `KnowledgeDocument` with `KnowledgeProvenance` and typed evidence. Solr, OpenSearch, and Qdrant consume projected representations with different schemas and capabilities. Source identity needs to remain stable across those representations.

## Decision

Canonical Repository Knowledge is backend-neutral. Projection compiles it into rebuildable search/vector representations. Vendor schema belongs to the adapter; projected stores do not own the meaning of a source document. A future graph store would follow the same rule.

## Consequences

Portability, testability, rebuild safety, and source provenance have one clear owner. Projection and rebuild consume work; serializer versions and fingerprints require discipline; each adapter must translate from neutral records.

## Alternatives considered

Backend-native canonical storage or a vendor-specific canonical schema would couple evidence identity to that vendor. Treating a search index as source of truth would make backend replacement and rebuild change source semantics. These alternatives were rejected by the repository contract boundary.

## Related issues/PRs

Repository extraction records in [history](../history/extraction/README.md); [module-boundary work #44](../migrations/issue-44-equivalence.md); current [repository knowledge](../architecture/repository-knowledge.md) and [projection](../architecture/projection.md).
