# Architecture overview

**Status: current, except where marked as direction.** FetchCore acquires, structures, indexes, and retrieves repository evidence. Its central rule is **retrieval ≠ satisfaction ≠ authority**: a result, score, or source label describes an acquisition observation. The consumer decides whether it has enough evidence, whether an answer is correct, and whether execution or approval is authorized. FetchCore does not implement an agent lifecycle.

```text
Repository sources
  → registry / collections → RepositoryIndexer
  → canonical Repository Knowledge → KnowledgePacks / typed relationships
  → projection → search and vector stores
  → retrieval backend port → application service → CLI / MCP / SDK
```

[Canonical Repository Knowledge](repository-knowledge.md) preserves source identity independently of a search engine. [Projection](projection.md) serializes it for rebuildable stores. [Retrieval](retrieval.md) acquires candidates; [adapters and composition](adapters-and-composition.md) select concrete implementations, and [serving](serving.md) exposes bounded operations. Repository policy and operator runtime configuration have different owners.

**Optional GraphProjection v1 (issue #30):** in addition to existing lexical/vector projections, the Solr adapter can publish a separate structural graph collection containing only resolved canonical relations, with manifest-pinned consistency checks. An opt-in, bounded graph navigation operation returns candidate paths and typed provenance, not proof of runtime execution. It does not replace `find_symbol`, `get_related` or standard retrieval and does not make graph mandatory. See [GraphProjection v1](graph-projection-v1.md) and its [network-path benchmark follow-up](../benchmarks/results/graph-projection-v1-network-followup-2026-11-23.md).

**Direction, not current capability:** federated multi-repository result fusion and general HTTP serving are not part of the present serving contract. The code and [contracts reference](../reference/contracts.md) define current behavior.
