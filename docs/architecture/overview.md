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

**Direction, not current capability:** a graph store may become another optional projection; federated multi-repository result fusion, structural graph lookup, and HTTP serving are not part of the present serving contract. The code and [contracts reference](../reference/contracts.md) define current behavior.
