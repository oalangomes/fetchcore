# Projection and ranking core extraction

Tracking: #5

## Upstream baseline

```text
oalangomes/AgentsOrchNext
2c85d455c1a8ceed53e128068d0c1da1a5e048cd
```

This slice extracts the backend-neutral representation and ranking core after
FetchCore established standalone repository and retrieval contracts.

## Extracted

- Projection v3 compiler and canonical reader;
- Projection v3 matching semantics;
- embedding provider/serialization contracts;
- deterministic fusion and RRF kernel;
- reranking contracts and deterministic kernel;
- rerank query serialization;
- post-acquisition reranking over FetchCore `SearchResult`.

## Compatibility

The extraction intentionally preserves:

- Projection v3 version and contract fingerprint;
- embedding serialization version;
- fusion/RRF behavior and default `k=60`;
- existing rerank query protocol identity/version during M0 extraction;
- candidate-set invariants for post-acquisition reranking.

The legacy rerank query identifier `agentsorch.rerank-query` is intentionally
retained in M0 because it is part of the serialized scoring query and changing it
would change behavior/fingerprints during the extraction. Renaming that protocol
is a later explicit compatibility migration, not part of repository separation.

## Deliberately deferred

`qdrant_sparse.py` is not extracted in this slice. Although its tokenization is
deterministic, the current contract contains Qdrant-specific vector names,
modifier semantics and REST representation. It moves with the Qdrant adapter in
the backend slice.

Also deferred:

- Solr/OpenSearch/Qdrant storage/network adapters;
- operational index contract/synchronization/orchestration;
- AgentsOrch adapter;
- Graph;
- MCP;
- new ranking experiments.

## Architectural boundary

```text
canonical Repository Knowledge
        ↓
Projection v3
        ↓
embedding / lexical-dense representation
        ↓
fusion / RRF
        ↓
optional post-acquisition reranking
        ↓
SearchResult[]

consumer-specific normalization happens outside FetchCore core
```

Reranking may reorder only the already acquired bounded candidate set. It may
not retrieve, synthesize or admit new candidates.
