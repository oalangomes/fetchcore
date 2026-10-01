# Issue #44 hardcode audit

The unit counted below is an operational or identity **decision group**, not
every occurrence of a literal. The review covered paths, environment names,
cache roots, URLs, ports, collection names, timeouts, batches, scan limits,
candidate windows, embedding model/dimensions, provider/backend names,
serializer selection, MCP limits, extractor/knowledge-pack registration, and
versioned identifiers in `src/fetchcore`.

| Classification | Reviewed groups |
| --- | ---: |
| DOMAIN_CONSTANT | 2 |
| VERSIONED_IDENTITY | 7 |
| SAFE_DEFAULT | 5 |
| CONFIGURABLE_POLICY | 6 |
| VENDOR_SPECIFIC | 7 |
| LEGACY_EXTRACTION_RESIDUE | 3 |
| ACCIDENTAL_HARDCODE | 1 |
| **Hardcodes reviewed** | **31** |

The groups and their owners are:

- **DOMAIN_CONSTANT (2):** canonical knowledge/evidence field vocabulary;
  `SearchMode`/capability semantics. These define contract meaning, not runtime
  tuning.
- **VERSIONED_IDENTITY (7):** Projection v3 schema/fingerprint; embedding
  serialization version/fingerprint; indexing contract version/fingerprint;
  fusion normalization/schema identity; reranking input/result schema identity;
  Qdrant sparse analyzer identity; repository/document/orchestration policy
  versions. None changed because of file moves.
- **SAFE_DEFAULT (5):** serving safety bounds; repository scan admission defaults;
  document admission defaults; orchestration concurrency bounds; local lexical
  safe-scan defaults. Their existing values are retained.
- **CONFIGURABLE_POLICY (6):** MCP repository binding environment; repository
  cache root; Git source timeout; repository-local indexing policy; document
  indexing policy; typed retrieval dense/hybrid/reranking configuration. These
  remain controlled by callers, environment at the serving/source boundary, or
  explicit policy files.
- **VENDOR_SPECIFIC (7):** Solr URL/collection/transport bounds; OpenSearch
  URL/index/transport bounds; Qdrant URL/collection/transport bounds; vector
  dimensions and mapping fields; vendor query/filter syntax; snapshot/control
  records; Ollama URL/model/request bounds. These stay in their adapters.
- **LEGACY_EXTRACTION_RESIDUE (3):** `AGENTSORCH_REPOSITORY_CACHE`,
  `.agentsorch/indexing.yaml`, and `.agentsorch/document-indexing.yaml` remain
  explicit compatibility inputs. New output and preferred configuration use
  FetchCore names.
- **ACCIDENTAL_HARDCODE (1):** the Ollama URL environment name and fallback were
  duplicated in Solr and OpenSearch factories. One Ollama adapter now owns the
  lookup and reads the existing `OLLAMA_URL` with the same default.

## Relevant before → after decisions

| Before | Classification | After and reason | Compatibility impact |
| --- | --- | --- | --- |
| `MAX_SEARCH_LIMIT=100`, `MAX_SOURCE_LINES=400`, `MAX_SOURCE_BYTES=1_000_000`, and context cap `100` were read directly by the service. | SAFE_DEFAULT / CONFIGURABLE_POLICY | `ServingLimits` owns these defaults and is injectable into `RetrievalApplicationService`; validation rejects invalid limits. | Default behavior and error categories remain the same. |
| Solr and OpenSearch each read `OLLAMA_URL` and repeated `http://localhost:11434`. | ACCIDENTAL_HARDCODE / VENDOR_SPECIFIC | `backends.ollama.configured_ollama_provider` owns the environment lookup and uses `OllamaEmbeddingSettings.url` as the single default. | Same env name, fallback URL, model, timeout, batch, and unsupported-provider error. |
| `repository/index_operations` added shell and HTML extractors after calling the structural registry. | CONFIGURABLE_POLICY | `repository/extractors/registry.py` composes all built-in source extractors in one place, still returning a fresh mutable mapping for caller extension. | Same default extractor set, selection order, and indexing fingerprint. |
| `repository/index_contract` combined canonical hash calculation with Solr snapshot types. | VERSIONED_IDENTITY / VENDOR_SPECIFIC | `repository/index_fingerprint` owns the canonical hash; `backends/solr_index_contract` owns Solr persistence. | Version `23` and captured hash unchanged; old imports remain. |
| Extractor type paths came from their old Python modules. | VERSIONED_IDENTITY | Moved extractor classes explicitly retain historical `__module__` values. | Persisted indexing fingerprints stay byte-for-byte identical. |

The fixed 768-dimensional Solr/OpenSearch mappings are intentionally still
vendor-specific schema constraints. Treating them as a generic user preference
would make an incompatible persisted index appear reusable. A later schema
version/migration can alter them explicitly.
