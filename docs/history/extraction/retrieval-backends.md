# Retrieval backend extraction

Tracking: #7

## Upstream baseline

```text
oalangomes/AgentsOrchNext
2c85d455c1a8ceed53e128068d0c1da1a5e048cd
```

This slice extracts the proven serving/index backend adapters after FetchCore
established canonical Repository Knowledge, retrieval/search contracts, Projection
v3, embedding, fusion and reranking.

## Extracted adapters

- local semantic/lexical;
- Solr;
- OpenSearch;
- Qdrant dense/hybrid;
- Qdrant sparse lexical representation;
- historical Solr Projection facade.

## Ownership

```text
FetchCore canonical knowledge
  -> Projection v3
  -> backend adapters
     - local
     - Solr
     - OpenSearch
     - Qdrant
  -> SearchResult[]
```

Each backend owns only engine-specific transport, settings, index/store mechanics
and query translation.

Canonical knowledge, Projection v3, query intent, match evidence, fusion and
reranking remain backend-neutral FetchCore contracts.

## Solr Projection compatibility

`fetchcore.backends.solr_projection` is retained only as a compatibility facade.

```text
SolrProjectionCompiler
  is ProjectionV3Compiler

SolrCanonicalDocumentReader
  is ProjectionV3CanonicalDocumentReader
```

It does not own or fork a separate projection model.

## Qdrant sparse placement

The deterministic sparse analyzer moves with the Qdrant slice because its current
public contract includes Qdrant-specific sparse vector representation, vector name
and IDF modifier semantics.

This does not make the canonical repository model Qdrant-aware.

## Deliberately deferred to #8

The following operational modules are still excluded:

- repository/index_contract.py
- repository/index_operations.py
- repository/incremental.py
- repository/incremental_persistence.py
- repository/document_incremental.py
- repository/orchestration.py

Those modules compose repository indexing with backend stores and belong to the
next operational extraction slice.

## Non-goals

- no AgentsOrch Context adapter;
- no MCP;
- no Graph;
- no ranking retuning;
- no GAC/Vivo-specific configuration;
- no new backend.
