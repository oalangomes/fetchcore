# Retrieval

The current backend-neutral port receives `SearchIntent`, `SearchScope`, and `SearchRequirement` values and returns `KnowledgeSearchResponse` with `SearchResult` evidence. `SearchIntent` currently has exact terms, semantic terms, hints, and typed signals. `SearchScope` carries repository, revision, paths, mode, result budget, settings, and optional retrieval configuration. These are acquisition inputs, not consumer satisfaction rules.

Current acquisition paths include local lexical scanning, backend lexical/BM25 retrieval, dense vector retrieval, and backend hybrid retrieval where capabilities allow. Hybrid combines lexical or sparse and dense candidates with RRF as the configured baseline. Post-acquisition reranking is optional and bounded by an explicit window. Capability checks reject unsupported modes; scores remain observations with match evidence and provenance.

The current `mode="targeted"` and exact terms are contract values, but a general identifier-first targeted router is **future direction** for #19. Required anchors, alternatives, branch semantics, adaptive budgets, graph expansion, and federated fusion are also future work. See [search intent](../concepts/search-intent.md) and [capabilities](../reference/capabilities.md).
