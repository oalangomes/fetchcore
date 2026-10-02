# Runtime configuration decision audit (#65)

The unit is a decision group, not each literal. This audit supersedes the #44
classification after #52 separated adapters by capability. Runtime precedence
is **safe defaults < explicit operator YAML < environment < explicit caller
overrides**. A present invalid value fails; it does not fall back.

| Decision | Classification | Current owner → owner | Default / override source | Persisted identity / compatibility |
| --- | --- | --- | --- | --- |
| Backend and provider IDs (`solr`, `qdrant-vector`, `ollama`, etc.) | BUILTIN_CATALOG | Composition → explicit registries | Code; test/SDK factories can be injected | IDs unchanged; factory ID mismatch fails |
| Selected backend, workspace, repository, revision, retrieval configuration | RUNTIME_CONFIG | MCP ENV parser → application runtime loader | Built-in local backend; operator YAML, ENV, caller | Legacy `FETCHCORE_MCP_*` accepted |
| Solr URL, collection, timeout, batch | SAFE_DEFAULT / RUNTIME_CONFIG | Solr settings and MCP JSON → runtime loader, then Solr settings | Local adapter defaults; operator YAML, `FETCHCORE_SOLR_*`, caller | Projection version and schema unchanged |
| OpenSearch URL, index, timeout, batch | SAFE_DEFAULT / RUNTIME_CONFIG | OpenSearch settings and MCP JSON → runtime loader, then OpenSearch settings | Local adapter defaults; operator YAML, `FETCHCORE_OPENSEARCH_*`, caller | Projection version and schema unchanged |
| Qdrant URL, collection, timeout, batch | SAFE_DEFAULT / RUNTIME_CONFIG | Qdrant settings and MCP JSON → runtime loader, then Qdrant settings | Local adapter defaults; operator YAML, `FETCHCORE_QDRANT_*`, caller | Sparse analyzer and projection identities unchanged |
| Qdrant API key | SECRET | Qdrant settings → runtime loader and adapter | ENV or explicit caller; no versioned default | No persistence change |
| Qdrant `embedding_url` and `embedding_model` | LEGACY_COMPATIBILITY | Qdrant settings → runtime compatibility translation | Existing MCP JSON/direct settings accepted; new input uses `embedding.ollama.url` and `retrieval.dense.embedding.model` | Direct Qdrant caller behavior retained |
| Ollama URL, timeout, request batch | SAFE_DEFAULT / RUNTIME_CONFIG | Composition `OLLAMA_URL` → typed `OllamaRuntimeSettings` | Local defaults; operator YAML, canonical ENV, caller | `OLLAMA_URL` retained as compatibility fallback |
| Selected embedding model | RUNTIME_CONFIG | `EmbeddingConfig` / Qdrant legacy setting → existing typed retrieval config | `embeddinggemma`; operator YAML retrieval, caller | Embedding serialization version/fingerprint unchanged |
| Repository indexing/document policy files | REPOSITORY_POLICY | Repository source loaders → unchanged | `.fetchcore/indexing.yaml`, `.fetchcore/document-indexing.yaml` | Legacy `.agentsorch` paths unchanged; never read as runtime endpoints |
| Repository cache root / Git process environment | REPOSITORY_POLICY / LEGACY_COMPATIBILITY | `repository/git_source.py` → unchanged source boundary | `FETCHCORE_REPOSITORY_CACHE`, legacy name | No runtime provider authority |
| Vector dimensions, schema fields, projection/index/serializer IDs | VERSIONED_IDENTITY | Projection and adapters → unchanged | Code only; versioned migrations required | Byte identities preserved |
| Retrieval semantics, RRF strategy, safety bounds | DOMAIN_CONSTANT / SAFE_DEFAULT | Retrieval/application contracts → unchanged | Typed retrieval and serving contracts where applicable | No tuning in #65 |
| Duplicate ad hoc Ollama environment lookup in composition | ACCIDENTAL_HARDCODE | Composition → application runtime loader | Canonical `FETCHCORE_OLLAMA_URL`, legacy `OLLAMA_URL` | Behavior for MCP bootstrap preserved |

`os.environ` / `os.getenv` inventory in `src/fetchcore` after this change:

- `application/runtime_config.py` reads the operator process environment once
  for runtime bootstrap and explicit `FETCHCORE_CONFIG` path.
- `repository/git_source.py` reads cache configuration and copies process
  environment for Git subprocesses. That is a repository source boundary.
- `serving/mcp.py`, `application/composition.py`, and retrieval/embedding
  adapters do not discover environment values.
- The operational indexing CLI uses Click's explicit `FETCHCORE_INDEX_*`
  options, including the JSON backend setting input retained for the #63
  self-dogfood workflow. Its indexing contract is separate from MCP serving.

The operator runtime YAML is only read from an explicit `path` argument or
`FETCHCORE_CONFIG`. No `.fetchcore` file inside a cloned repository is a runtime
configuration source. See [runtime configuration](../guides/runtime-configuration.md).
