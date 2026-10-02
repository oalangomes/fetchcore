# Add a retrieval backend

1. Implement `KnowledgeSearchBackend.search(intent, scope, requirements)` in `adapters/retrieval/<backend>/`. Preserve source identity, provenance, and match observations in `SearchResult`.
2. Declare `RetrievalBackendCapabilities`; reject unavailable or unrepresentable behavior explicitly.
3. Keep vendor query and response syntax inside the adapter. Use shared projection and retrieval contracts for meaning.
4. Add typed deployment settings and validation at the operator runtime boundary if the adapter needs them.
5. Register a code-owned factory in `application/composition.py` and its built-in ID in the runtime catalog. Inject an `EmbeddingProvider` through the existing port if dense retrieval needs one.
6. Add contract, translation, error, and provenance tests. Compare retrieval quality, latency, and cost with the [benchmark protocol](../benchmarks/protocol.md).

Adding a backend does not normally change canonical knowledge, `SearchIntent`, MCP transport, or an embedding provider implementation. If a real missing domain capability requires a contract change, version and document it separately. See [adapter architecture](../architecture/adapters-and-composition.md) and [ADR-0002](../adr/ADR-0002-capability-adapters.md).
