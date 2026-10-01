# Module boundaries

## Inventory before issue #44

The pre-migration source has 39,208 Python lines. The following map records the
responsibility clusters and their principal inbound/outbound imports. Paths in
this table are the original public paths; the compatibility section records how
they remain usable.

| Original modules | Responsibility and dependencies | Boundary concern |
| --- | --- | --- |
| `search`, `signals`, `evidence`, `retrieval` | Public intent, scope, result, provenance, configuration, and capability contracts. Imported by adapters, application, baseline, projection, and tests. `search` imports the other contracts and telemetry. | Four related contracts live at the package root. |
| `embedding`, `fusion`, `reranking*` | Provider protocol and Ollama adapter, vector serialization, RRF, reranking contracts/execution. Imported mainly by remote adapters and tests. `embedding` imports projection and search. | Provider transport and canonical embedding serialization share one module. |
| `projection`, `projection_semantics` | Projection v3 serialization/hydration and common query mapping/match evidence. Imported by all remote adapters. `projection` imports canonical repository documents and search errors. | Large root modules with a coherent projection owner. |
| `repository/indexing`, `evidence`, `knowledge*`, `safe_scan`, `document*`, `incremental*` | Canonical knowledge, safe admission, document indexing, and incremental state. Imported by projection, adapters, and operations. | A single namespace contains several stages. |
| `repository/structure`, `dart`, `groovy`, `shell`, `html`, `sap_commerce*` | Structural/language/framework extractors and knowledge packs. Imported by indexer and operations. | Extractor registration is spread between `structure` and operations. |
| `repository/index_contract`, `index_operations`, `incremental*` | Solr snapshot compatibility and operational sync. Import concrete `backends.solr`. | These are Solr integrations, despite the generic repository namespace. |
| `backends/local_semantic`, `solr`, `opensearch`, `qdrant_vector` | Concrete storage/query adapters, backend settings, transport clients, and diagnostics. Import repository, retrieval, projection, and telemetry. | Solr, OpenSearch, and Qdrant are large files; split only on a stable responsibility seam. |
| `application`, `mcp_server` | Repository-scoped retrieval/source service, backend composition, environment loading, MCP transport. MCP imports application, never a backend. | Service and transport are already conceptually separate but remain root modules. |
| `baseline`, `telemetry` | Frozen evaluation protocol and retrieval observations. Imported by benchmark runner/backends. | Stable cross-cutting support. |

The principal dependency knot is `repository/indexing` ↔ structural extractors:
the indexer defines the `SymbolExtractor`/`SymbolSpan` contract, while extractor
implementations import it. Default registration is late to avoid an import cycle.
The explicit concrete-backend imports from repository operations and incremental
modules are historical Solr integration, not canonical knowledge dependencies.

Public paths are those used by tests, the MCP entry point, benchmark runner, or
documented imports: `fetchcore.search`, `retrieval`, `signals`, `evidence`,
`embedding`, `fusion`, `reranking*`, `projection*`, `application`, `mcp_server`,
and repository modules. Private names beginning with `_` are implementation
details, except where an existing internal cross-module import needs a temporary
compatibility alias.

Persisted identifiers include Projection v3 and its contract fingerprint,
embedding serialization version `2`, indexing contract version `23`, reranking
and fusion schema versions, backend snapshot schemas, document/indexing policy
versions, and the Qdrant sparse analyzer fingerprint. Indexing fingerprints also
contain extractor class module names. These identities must stay fixed during
physical moves. The executable entry point is `fetchcore-mcp` at
`fetchcore.mcp_server:main`.

## Target ownership and dependency direction

```text
repository sources and extraction -> canonical repository knowledge
                                      -> projection -> backend adapters
retrieval contracts -----------------^       ^              |
retrieval execution -------------------------+--------------+
application service -> backend port -> concrete adapters (composition only)
serving/MCP -> application service
```

`retrieval` owns intent, evidence, typed configuration, fusion, reranking, and
embedding contracts. `projection` owns backend-neutral projection serialization
and matching. `repository` owns acquisition, extraction, canonical documents,
indexing, and persisted incremental state. `backends` owns vendor protocols and
schemas. `application` owns caller-facing retrieval/source operations and
composition. `serving` owns transport and environment loading. `baseline` and
`telemetry` retain their existing specialized responsibilities.

## Implemented package map

| Package | Owning modules and role |
| --- | --- |
| `knowledge` | `evidence` and `chunking`: canonical source-derived evidence and deterministic non-code chunks. |
| `repository` | Source materialization, safe scanning, policy, canonical documents/indexing, Git delta detection, and `index_fingerprint`. `extractors/languages`, `extractors/documents`, `extractors/frameworks/sap_commerce`, and `extractors/registry` hold implementation and composition points. |
| `retrieval` | `contracts`, `signals`, `evidence`, `config`, `embedding` serialization/protocol, `fusion`, and `reranking*`. |
| `projection` | `core` compiler/reader and shared `semantics`. |
| `backends` | Local, Solr, OpenSearch, Qdrant, and Ollama adapters; `solr_query`, `opensearch_query`, and `qdrant_query` translate/validate vendor requests and responses; `solr_*` integration modules own snapshot and synchronization behavior. |
| `application` | `service` owns caller-facing operations through `KnowledgeSearchBackend`; `composition` selects concrete factories; `index_operations` and `orchestration*` coordinate repository indexing. |
| `serving` | `mcp` loads MCP environment settings and calls only the application service. |

The only direct concrete-backend import under `repository` is the historical
`index_contract` compatibility shim. `repository.incremental` and
`retrieval.embedding` provide lazy compatibility attributes for moved Solr and
Ollama classes. Canonical functions in those modules do not depend on adapters.
The static top-level import graph has 133 modules, 234 import edges, and no
strongly connected component containing more than one module.

The application composition root supplies backend factories. Backend IDs select
implementations only there. Typed backend and retrieval settings own operational
defaults; adapters own vendor URLs, index names, dimensions, timeouts, and batch
sizes. MCP environment variables are read only by its serving adapter. Repository
policy loaders own `.fetchcore` configuration paths, including legacy-path
compatibility. No canonical contract interprets a vendor response or environment
variable.

New extractors belong under `repository/extractors` and implement the existing
`SymbolExtractor`/`StructuralExtractor` contract. A registry composes the default
implementations; knowledge packs remain source-oriented. New backends implement
`KnowledgeSearchBackend` and declare `RetrievalBackendCapabilities`, then get a
factory in application composition. New transports call
`RetrievalApplicationService` and do not implement retrieval policy.

Historical import paths remain compatibility aliases to the owning module.
Aliases contain no duplicate implementation. Removal requires a separate public
migration and compatibility window. Serialized IDs and fingerprints are not
renamed merely because the owning file moved.

The operational literal review and classification is in
[`hardcode-audit.md`](hardcode-audit.md).
The frozen baseline comparison is recorded in
[`issue-44-equivalence.md`](issue-44-equivalence.md).
