# Module boundaries

**Status: current after #44, #52, and #65.** Dependencies flow from repository source extraction and retrieval contracts toward application composition, then outward to concrete adapters and transports. Infrastructure types must not define canonical knowledge or retrieval meaning.

| Owner | Current responsibility |
| --- | --- |
| `repository` and `knowledge` | Safe scanning, extraction, KnowledgePacks, canonical documents, policy, incremental state. |
| `projection` | Backend-neutral projection serialization and query/match semantics. |
| `retrieval` | Intent, scope, evidence, configuration, capability, fusion, and bounded reranking contracts. |
| `adapters/retrieval` | Local, Solr, OpenSearch, and Qdrant retrieval and vendor translation. Solr operational index integration lives here. |
| `adapters/embedding` | Ollama implementation of the embedding port. |
| `application` | Repository-scoped service, operator runtime configuration, explicit backend/provider registries, and index operations. |
| `serving` | MCP transport bootstrap and tools. |
| `baseline` and `telemetry` | Frozen evaluation and retrieval observations. |

`fetchcore.backends` and several root modules remain compatibility imports. Their historical names do not change persisted serializer or projection identities. Application composition is allowed to import concrete adapters; `KnowledgeSearchBackend` and `EmbeddingProvider` keep the core independent of them. MCP calls the application service. The operational indexing CLI currently composes Solr explicitly; it is a narrower path than the general retrieval backend registry.

See [adapters and composition](adapters-and-composition.md), [runtime configuration](../reference/configuration.md), the [#44 equivalence record](../migrations/issue-44-equivalence.md), and the [#65 runtime audit](../migrations/issue-65-runtime-audit.md).
