# Operator runtime configuration

FetchCore serving configuration belongs to the operator. Give the MCP process
an explicit `FETCHCORE_CONFIG=/absolute/path/config.yaml`, or call
`load_runtime_config(path=...)` from an SDK bootstrap. FetchCore does not search
the repository workspace for this file. Repository-owned `.fetchcore` indexing
and document policy files cannot set runtime endpoints.

```yaml
version: 1
runtime:
  backend: qdrant-vector
  workspace: /workspace
  repository: owner/repo
retrieval:
  dense:
    enabled: true
    embedding:
      provider: ollama
      model: embeddinggemma
backends:
  qdrant-vector:
    url: http://qdrant:6333
    collection: fetchcore-vector
    timeout_seconds: 30
embedding:
  ollama:
    url: http://ollama:11434
    timeout_seconds: 30
    request_batch_size: 32
```

The format has its own version (`1`), independent of projection, indexing, and
embedding serializer versions. Unknown versions, unknown fields, malformed
mappings, invalid URLs, timeouts, and batches fail during bootstrap. A missing
field may use a safe default; an invalid field does not.

Precedence is:

```text
safe built-in defaults < explicit operator YAML < environment < explicit caller overrides
```

The application API accepts resolved values in memory:

```python
from pathlib import Path
from fetchcore.application import FetchCoreRuntimeConfig, service_from_runtime_config

runtime = FetchCoreRuntimeConfig(workspace=Path("/workspace"), repository="owner/repo")
service = service_from_runtime_config(runtime)
```

`load_runtime_config(path=..., environ=..., overrides=...)` offers the same
precedence to callers that want to load YAML. `overrides` uses the YAML shape.
No YAML or ENV is required for direct `FetchCoreRuntimeConfig` construction.

Simple runtime environment overrides include `FETCHCORE_BACKEND`,
`FETCHCORE_WORKSPACE`, `FETCHCORE_REPOSITORY`, `FETCHCORE_REVISION`,
`FETCHCORE_RETRIEVAL` (JSON compatibility input), `FETCHCORE_SOLR_URL`,
`FETCHCORE_SOLR_COLLECTION`, `FETCHCORE_OPENSEARCH_URL`,
`FETCHCORE_OPENSEARCH_INDEX`, `FETCHCORE_QDRANT_URL`,
`FETCHCORE_QDRANT_COLLECTION`, `FETCHCORE_QDRANT_API_KEY`,
`FETCHCORE_OLLAMA_URL`, `FETCHCORE_OLLAMA_TIMEOUT_SECONDS`, and
`FETCHCORE_OLLAMA_REQUEST_BATCH_SIZE`. Use YAML for structured retrieval and
backend settings. Keep secrets out of checked-in YAML; inject the Qdrant API key
through ENV or explicit caller values.

Historical `FETCHCORE_MCP_*` names remain accepted when the canonical runtime
name is absent. `OLLAMA_URL` is the fallback for `FETCHCORE_OLLAMA_URL`.
`FETCHCORE_MCP_BACKEND_SETTINGS` and `FETCHCORE_MCP_RETRIEVAL` remain JSON
compatibility inputs for existing MCP and dogfood invocations. A present
canonical name takes precedence over its legacy counterpart. The indexing CLI
continues to use `FETCHCORE_INDEX_*` and its controlled JSON settings for the
self-dogfood indexing workflow; this serving runtime file is not required for
benchmark or indexing reproduction.

The built-in backend catalog (`local-semantic`, `solr`, `opensearch`,
`qdrant-vector`) and embedding provider catalog (`ollama`) stay in code.
Provider and backend factories can be injected for tests or SDK composition;
factory identity mismatches fail explicitly.
