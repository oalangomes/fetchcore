# Qdrant OPERATIONAL preflight — 2026-10-17

This page records the historical preflight state. The subsequent real
[Qdrant OPERATIONAL acceptance](../benchmarks/results/qdrant-operational-acceptance-2026-10-18.md)
documents the rebuild, incremental sync, no-op, retrieval, and source gate.

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

## Follow-up — corrected WSL endpoint and resource reconciliation

### Effective Ollama endpoint

The indexing shell had neither `FETCHCORE_CONFIG` nor `FETCHCORE_OLLAMA_URL`
set, and no standard `~/.config/fetchcore` runtime YAML was present. The saved
shared-endpoints file is a DEV/BENCH override, not an OPERATIONAL source; it
contains `host.docker.internal:11434`, which was unreachable from this WSL
process. The generated operational route has the same container-oriented
default, so it was not used for the host-process embedding probe.

Instead, the WSL default-route gateway was resolved at runtime and passed
explicitly to `shared_infra.py --ollama-url`; no address was fixed in code or
persisted globally. The resolved `http://172.21.32.1:11434` endpoint responded
to `/api/tags` and listed `embeddinggemma:latest`. A real call through
FetchCore's `OllamaEmbeddingProvider` returned one 768-dimensional vector.

| Identity | Resolved value |
| --- | --- |
| Provider / model / dimensions | `ollama` / `embeddinggemma` / 768 |
| Runtime endpoint source | explicit `shared_infra.py --ollama-url`, derived from the current WSL default route |
| Runtime config source | `environment` (`FETCHCORE_CONFIG` and the parent shell's `FETCHCORE_OLLAMA_URL` were unset) |
| Projection version / fingerprint | `3` / `sha256:cfd4a63f85c8e4034bf17f0f82f08a47c717d52e75a9180ffb1eb6cad719e039` |
| Serializer mode / version / fingerprint | `projection-v3` / `2` / `sha256:5f859289b8b3a594e924545edb1c3ae6178b77ea22cf3ff224d43c5ec7aeb43a` |

The probe only generated an in-memory test vector. It did not index a document,
write a Qdrant point, install a model, or start an Ollama service.

### Inventory reconciliation and schema preparation

The persisted `qdrant-aliases.json` and `qdrant-resources-before.json` from
2026-10-16 were captured against the shared Qdrant before the OPERATIONAL
container was created; they describe the shared DEV/BENCH resources, not the
OPERATIONAL alias. The saved shared endpoint's old `127.0.0.1:6333` is not the
current shared host port (`16333`). The live OPERATIONAL request was separately
verified against `127.0.0.1:36333`.

The current OPERATIONAL container and volume are the expected project-owned
resource: container `16f5dd0eb133…`, project `fetchcore-operational`, owner
`fetchcore-shared-infra-v1`, volume
`fetchcore-operational_qdrant-data`, mounted at `/qdrant/storage`. The container
and volume creation timestamps match the 2026-10-16 provisioning window. Current
shared Qdrant (`16333`) has distinct DEV/BENCH aliases; the dedicated BENCH
Qdrant is also a separate resource. No Qdrant container or volume was replaced.

Before preparation, the correct OPERATIONAL endpoint returned no collections
and no aliases. The Lab API/MCP containers report Solr as their selected backend;
no consumer of this OPERATIONAL Qdrant resource or local indexing process was
observed. The earlier 2026-10-16 follow-up documented a prepared, empty
OPERATIONAL alias/schema; the current absence is inconsistent with that record.
The available pre-operational alias inventory cannot explain the difference, so
the cause remains **unresolved**. This report does not infer data loss; the
volume was not deleted, replaced, or recreated.

Given the currently empty inventory and matching ownership, the existing
explicit `shared_infra.py prepare --create-if-missing --vector-dimensions 768`
was used once to create only the empty Qdrant collection and alias:

- Alias: `fc-operational-fetchcore-635ab7352c2850f2-635ab7352c28`
- Collection: `fc-operational-fetchcore-635ab7352c2850f2-635ab7352c28-infra-v3`
- Dense schema: 768 dimensions, cosine; FetchCore sparse schema: validated.
- A subsequent read-only `prepare` returned `reused`; the Qdrant adapter's
  schema validation passed, exact point count was 0, and the repository snapshot
  is still absent.

This was **schema preparation only**. No points, snapshot, or repository content
were written. Since #191 remains unmerged, application/CLI/MCP status, plan,
rebuild, sync, dense/hybrid retrieval, and `get_source` remain untested. Overall
acceptance is still **INCONCLUSIVE**.
