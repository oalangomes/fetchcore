# OpenSearch operational preflight — 2026-10-17

Decision: **INCONCLUSIVE for operational promotion**. No operational index writes
were made. Retrieval quality and indexing metrics were not measured because the
required dense provider was unavailable and the target alias did not exist.

## Read-only evidence

| Check | Observation |
| --- | --- |
| Target | `oalangomes/fetchcore`, `http://127.0.0.1:39200`, alias `fc-operational-fetchcore-635ab7352c2850f2-635ab7352c28` |
| Source base | Local and remote `master` were both `5cea7ad1b8b915dd5b1cd56821fbf0805aa86a3a` at preflight |
| Container ownership | `fetchcore-operational-opensearch-1`, Compose project `fetchcore-operational`, service `opensearch`, data volume `fetchcore-operational_opensearch-data` |
| Backend health | OpenSearch 3.8.0, cluster `green`, one data node, zero pending tasks, no in-flight fetch |
| Repository alias | `GET /_alias/<target>` returned 404; `_cat/aliases` listed no aliases |
| Repository snapshot | No repository index existed; `_cat/indices` listed only OpenSearch plugin indexes |
| Concurrent indexing | `_tasks` returned no reindex, bulk, or indexing tasks at observation time |
| Capacity | 6.5 GiB available host memory, 793 GiB free disk, operational OpenSearch about 1.15 GiB resident allocation and under 1% CPU at observation time |
| Embedding provider | `127.0.0.1:11434` refused connection; Ollama/embeddinggemma 768 could not be reached from the process host |
| CLI bootstrap | Read-only validation returned `index_missing` for the target alias |
| CLI status | `initial` / `absent`; indexing fingerprint `sha256:4a66bd379a149aab3f3ee7f59236f53a5f08ccda7c7ac7c67ddc5ea100824bfe`; Projection v3 fingerprint `sha256:cfd4a63f85c8e4034bf17f0f82f08a47c717d52e75a9180ffb1eb6cad719e039` |

The alias was absent, so there was no active revision, checksum, or consumer
binding to verify. The absence also means a real retrieval smoke and controlled
quality comparison against the operational snapshot were impossible. Existing
Solr and Qdrant services were not changed.

## Follow-up gate

Make Ollama/embeddinggemma reachable to the indexing process and verify the
768-dimensional response. Confirm ownership and consumers for the exact alias,
then run `bootstrap → status → plan → rebuild → status → plan/noop → sync →
search → get_source` against the selected revision. Record document writes,
deletes, generated and reused embeddings, latency, snapshot identity, revision,
checksum, and retrieval evidence before declaring OpenSearch operationally ready.
