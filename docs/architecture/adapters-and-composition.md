# Adapters and composition

`KnowledgeSearchBackend` is the retrieval port implemented under `adapters/retrieval/{local,solr,opensearch,qdrant}`. `EmbeddingProvider` is a separate port implemented by `adapters/embedding/ollama`. Retrieval adapters request embeddings through that port; they do not select a concrete provider by importing Ollama.

`application/composition.py` owns explicit `BackendRegistry` and `EmbeddingProviderRegistry` factories. It is the boundary allowed to know concrete implementations. Built-in IDs are code-owned, and unknown IDs or factory identity mismatches fail explicitly. The runtime loader resolves settings and hands typed values to composition; adapters should not discover their own environment settings.

Backend capability differences are declared through `RetrievalBackendCapabilities`. A backend that cannot represent a requested capability reports it rather than silently substituting another mode. See [ADR-0002](../adr/ADR-0002-capability-adapters.md) and [adding a backend](../guides/adding-a-backend.md).
