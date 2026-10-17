# OpenSearch operational preflight — 2026-10-17

Decision: **PASS for the initial OPERATIONAL lifecycle gate** on the pinned
FetchCore `master` revision. This is an operational smoke, not a comparative
retrieval-quality benchmark or approval of the wider #123 backend matrix.

## Configuration and ownership

The process ran in WSL (`/proc/version`); `FETCHCORE_CONFIG`,
`FETCHCORE_OLLAMA_URL`, and legacy `OLLAMA_URL` were unset. The runtime loader
therefore reported `embedding.ollama.url: default`. Its built-in localhost URL
was not used. The current WSL default route resolved the Windows host as
`172.21.32.1`, and the existing Ollama service answered at
`http://172.21.32.1:11434`. That URL was passed explicitly to
`shared_infra.route(..., ollama_url=...)` for this run. The route's default
`host.docker.internal:11434` applies to Docker callers, not this WSL process.
No endpoint fallback or new Ollama instance was used.

`GET /api/tags` returned HTTP 200 and listed `embeddinggemma`; `POST /api/embed`
with that model returned HTTP 200, one vector of 768 dimensions. No vector
values or credentials are included here.

| Check | Observation |
| --- | --- |
| Repository and source | `oalangomes/fetchcore`, Git provider, `master@5cea7ad1b8b915dd5b1cd56821fbf0805aa86a3a` (same as remote `master` at preflight) |
| Target | OPERATIONAL OpenSearch `http://127.0.0.1:39200`, alias `fc-operational-fetchcore-635ab7352c2850f2-635ab7352c28` |
| Ownership | Container `fetchcore-operational-opensearch-1` and data volume `fetchcore-operational_opensearch-data`, Compose project `fetchcore-operational`, owner label `fetchcore-shared-infra-v1` |
| Health | OpenSearch 3.8.0, green, one node, zero pending tasks; no repository writer at preflight |
| Namespace before rebuild | Target alias absent; no `fc-operational-*` index; only OpenSearch plugin indexes existed |
| Consumers | Repository indexing workflow targets the separate OPERATIONAL Solr resource; no workflow or live alias referenced this OpenSearch target before creation |
| Policy | Explicit initial rebuild only for this empty, owned OPERATIONAL namespace; DEV/BENCH, Solr, and Qdrant were not modified |

## Gate execution

The controlled variable was the initial population of the empty OpenSearch
repository alias using the existing Projection v3, `ollama/embeddinggemma`
(768 dimensions), and explicit hybrid retrieval configuration. No baseline
V0.1–V0.4 artifacts or fingerprints were changed.

| Step | Before | After |
| --- | --- | --- |
| `bootstrap` | `index_missing` | Valid schema and 768-dimensional embedding probe |
| `status` | `absent`, mode `initial` | `current`, schema/projection compatible |
| `plan` | `initial`, 5,081 document upserts | `noop`, 0 upserts/deletes, 5,081 unchanged |
| Explicit `rebuild` | No repository index or snapshot | 5,081 knowledge documents plus one snapshot record; one alias target |
| `sync` at same revision | Not applicable | `noop`, 0 backend writes, 0 deletes, 0 embeddings |
| Hybrid `search` and `source` | Unavailable | Search returned revision-bound candidates; source read succeeded for `src/fetchcore/application/indexing.py` |

The active snapshot records repository `oalangomes/fetchcore`, revision
`5cea7ad1b8b915dd5b1cd56821fbf0805aa86a3a`, corpus checksum
`09cb6b1154b708d6af8ef1fcc7623f9c70317be4508af6f93fcf2adee3184ca6`,
5,081 documents, Projection v3 fingerprint
`sha256:cfd4a63f85c8e4034bf17f0f82f08a47c717d52e75a9180ffb1eb6cad719e039`,
indexing fingerprint
`sha256:4a66bd379a149aab3f3ee7f59236f53a5f08ccda7c7ac7c67ddc5ea100824bfe`,
and serializer fingerprint
`sha256:5f859289b8b3a594e924545edb1c3ae6178b77ea22cf3ff224d43c5ec7aeb43a`.
The alias points to exactly one concrete generation. OpenSearch reports 5,082
records because the snapshot is stored alongside the knowledge documents.

The rebuild reported 5,081 backend writes and 0 deletes. Its indexing duration
was 184.5 s, including 170.3 s for 26 embedding requests and 5,081 vectors
(29.8 vectors/s); command wall time was 191.5 s. These are observations on a
shared host, not causal latency estimates. One hybrid search completed in 193 ms
of reported backend work; no latency distribution was measured.

## Defect found during postflight

OpenSearch 3.8 adds `"parameters": {}` to the returned `knn_vector.method`
mapping even when it was absent from the create request. Exact dictionary
comparison caused `bootstrap` to reject the index just promoted by `rebuild`.
The adapter now accepts only that empty normalization while rejecting changed
method fields or nonempty parameters. The regression test covers both cases.
Local verification: 1,063 tests passed, 9 optional integrations skipped;
lint, typecheck (180 source files), offline source/wheel build, and
`git diff --check` passed.

## Limits

The search/source smoke proves operational access and provenance on this public
repository snapshot. It does not measure EvidenceRecall, ContextYield, or a
quality delta against Solr, and it does not promise universal ANN recall.
OPERATIONAL consumers must opt into the new OpenSearch alias explicitly; the
existing Solr workflow remains unchanged. No reset, migration, or index deletion
was performed. The local raw command outputs are retained outside the repository
under `~/.local/state/fetchcore/opensearch-pr191/` for operator inspection.
