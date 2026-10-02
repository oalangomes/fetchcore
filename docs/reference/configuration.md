# Configuration reference

**Serving runtime:** `load_runtime_config(path=..., environ=..., overrides=...)` in `fetchcore.application.runtime_config` loads version `1` operator YAML. The top-level sections are `version`, `runtime`, `retrieval`, `backends`, and `embedding`. Precedence is safe defaults < operator YAML < environment < explicit caller overrides. Unknown versions, fields, IDs, invalid URLs, timeouts, and batches fail during bootstrap.

`runtime` selects a built-in backend and may set workspace, repository, revision, knowledge types, and repository paths. `retrieval` maps to `RetrievalConfig` (`dense`, `hybrid`, `reranking`, `ltr`). `backends` holds settings by built-in ID. `embedding.ollama` holds its URL; `retrieval.dense.embedding` owns provider, model, timeout, and request batch. Qdrant `embedding_url` and `embedding_model` remain legacy compatibility inputs.

`FETCHCORE_CONFIG` points to the operator file. `FETCHCORE_BACKEND`, `FETCHCORE_WORKSPACE`, `FETCHCORE_REPOSITORY`, `FETCHCORE_REVISION`, backend endpoint/collection variables, and Ollama overrides are supported; see [the runtime guide](../guides/runtime-configuration.md) for current names and a complete example. Historical `FETCHCORE_MCP_*` values remain compatibility inputs. `FETCHCORE_INDEX_*` configure the separate operational indexing CLI.

Repository-owned `.fetchcore` policy controls corpus admission and indexing behavior, not runtime network endpoints or provider selection. Persisted projection/schema identities are versioned contracts, not arbitrary runtime preferences. See [ADR-0003](../adr/ADR-0003-operator-runtime-configuration.md).
