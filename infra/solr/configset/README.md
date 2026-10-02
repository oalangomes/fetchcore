# FetchCore Solr configset

This configset is the runtime schema for the FetchCore Solr Projection v3 adapter.

It was extracted from the behavior-proven AgentsOrchNext Solr configset used by the retrieval baseline. The schema name was renamed from `agentsorch-knowledge` to `fetchcore-knowledge`; field types, fields, BM25 parameters, vector dimensions, analyzers and request-handler behavior are preserved.

The configset is infrastructure for a rebuildable projection. It is not canonical repository knowledge and must not introduce product semantics outside the backend adapter contract.

When this configset changes, validate compatibility against the projection fingerprint/schema checks and rerun the appropriate retrieval baseline before promotion.
