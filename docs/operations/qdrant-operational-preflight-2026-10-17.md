# Qdrant OPERATIONAL preflight — 2026-10-17

## Decision

**INCONCLUSIVE; no indexing writes were made.** This records a read-only
preflight for #123, not an operational indexing or retrieval acceptance result.
The application lifecycle is still dependent on #191, and the configured
embedding service was unavailable.

## Resource identity and inventory

- Compose project: `fetchcore-operational`.
- Container: `fetchcore-operational-qdrant-1`, image `qdrant/qdrant:v1.19.0`.
- Container owner label: `io.fetchcore.owner=fetchcore-shared-infra-v1`.
- Persistent volume: `fetchcore-operational_qdrant-data`, with matching project
  and owner labels.
- REST endpoint: `http://127.0.0.1:36333`; gRPC endpoint:
  `127.0.0.1:36334`.
- Readiness: `/readyz` reported all shards ready; server version was `1.19.0`.
- Read-only collection and alias inventories were both empty. The expected
  alias `fc-operational-fetchcore-635ab7352c2850f2-635ab7352c28` was absent,
  with no concrete collection or repository snapshot available to inspect.
- No local FetchCore/Python/Qdrant/Ollama indexing process was observed. Since
  no alias or collection exists, no consumer of this repository resource could
  be verified; this check does not establish absence of external clients.

## Embedding and host capacity

- Ollama at `127.0.0.1:11434` refused the connection. Provider, model
  `embeddinggemma`, and 768-dimensional output could not be validated.
- No model was downloaded, installed, or started.
- Host capacity at inspection: 19 GiB memory total, approximately 5.8 GiB
  available, and 793 GiB free disk (18% used).

## Safety outcome

The container and persistent volume are owned by the isolated OPERATIONAL
Compose project. However, the alias/schema and snapshot are absent, Ollama is
unavailable, and the shared application lifecycle in #191 has not yet merged.
Therefore the explicit rebuild and the application/MCP/CLI
`bootstrap → status → plan → rebuild → status → sync → noop → dense/hybrid → get_source`
sequence were not run. No Qdrant OPERATIONAL, DEV, or BENCH data was written,
and OpenSearch and Solr were not touched.

Retry the preflight after #191 is integrated and the OPERATIONAL alias/schema
and embedding provider are available. Do not infer a current index or successful
retrieval from this report.
