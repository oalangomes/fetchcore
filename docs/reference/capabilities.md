# Retrieval capabilities

`RetrievalBackendCapabilities` advertises `lexical`, `dense`, `hybrid`, `reranking` (compatibility field), and `ltr`. `validate_backend_retrieval` requires lexical for lexical mode, dense for dense mode, and both dense and hybrid for hybrid mode. LTR is checked when enabled. Reranking is resolved after candidate acquisition and is not a backend capability gate.

| Built-in ID | Adapter | Role |
| --- | --- | --- |
| `local-semantic` | `adapters/retrieval/local` | Local repository scanning and lexical acquisition. |
| `solr` | `adapters/retrieval/solr` | Solr retrieval and current operational index synchronization. |
| `opensearch` | `adapters/retrieval/opensearch` | OpenSearch retrieval. |
| `qdrant-vector` | `adapters/retrieval/qdrant` | Qdrant dense and hybrid vector/sparse acquisition. |
| `ollama` | `adapters/embedding/ollama` | Embedding provider, separate from retrieval backends. |

Inspect each adapter's `retrieval_capabilities` for exact mode support. An unsupported requested mode raises a typed error; there is no implicit dense/lexical substitution. See [retrieval configuration](configuration.md).
