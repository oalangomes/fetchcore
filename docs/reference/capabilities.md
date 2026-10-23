# Retrieval capabilities

`RetrievalBackendCapabilities` advertises `lexical`, `dense`, `hybrid`, `reranking` (compatibility field), and `ltr`. `validate_backend_retrieval` requires lexical for lexical mode, dense for dense mode, and both dense and hybrid for hybrid mode. LTR is checked when enabled. Reranking is resolved after candidate acquisition and is not a backend capability gate.

| Built-in ID | Adapter | Role |
| --- | --- | --- |
| `local-semantic` | `adapters/retrieval/local` | Local repository scanning and lexical acquisition. |
| `solr` | `adapters/retrieval/solr` | Solr retrieval and operational index synchronization. |
| `opensearch` | `adapters/retrieval/opensearch` | OpenSearch retrieval and operational index synchronization. |
| `qdrant-vector` | `adapters/retrieval/qdrant` | Qdrant dense and hybrid vector/sparse acquisition and operational index synchronization. |
| `ollama` | `adapters/embedding/ollama` | Embedding provider, separate from retrieval backends. |

Inspect each adapter's `retrieval_capabilities` for exact mode support. An unsupported requested mode raises a typed error; there is no implicit dense/lexical substitution. See [retrieval configuration](configuration.md).

`canonical_query` advertises direct canonical-text consumption, independently of the backend's native analysis. `structured_anchors` and `boolean_expressions` independently advertise required anchors and declared boolean groups on the lexical channel. Solr/OpenSearch advertise all three; local and Qdrant advertise canonical text only. Dense participation does not inherit lexical requirement support. `validate_intent_translation` rejects unsupported required constructs before execution. Unsupported optional additions are reported per channel and do not reject dense/hybrid. No expansion capability flag is needed: existing lexical/sparse translators consume expansions and dense reports them as unconsumed. See [SearchIntent v2](../concepts/search-intent.md) for the per-channel matrix and compatibility rules.

The intent capability decomposition uses `canonical_query`, `structured_anchors`, and `boolean_expressions`; acquisition flags (`lexical`, `dense`, `hybrid`, `reranking`, `ltr`) are unchanged. All default to `False`. This replaces the unmerged v2 `lexical_requirements` field: adapters implementing that draft must declare anchors and boolean expressions separately. No persisted projection or embedding identity changes.

`targeted_lookup` independently advertises bounded exact acquisition before broad channels. Local, Solr and OpenSearch implement this optional port; Qdrant reports it unavailable. See [targeted retrieval](../concepts/targeted-retrieval.md) for policy, scope and explicit fallback.
