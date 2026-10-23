# Projection

`projection/core.py` compiles canonical `KnowledgeDocument` content into `ProjectedKnowledgeRecord` and reads the persisted representation. Retrieval owns matching and query semantics over those records in `retrieval/projection_matching.py`. `projection/semantics.py` and `projection_semantics.py` are historical import aliases to that retrieval module. Search and vector stores hold rebuildable projections, while canonical Repository Knowledge retains the source trace. A graph projection is a possible future addition, not a required current hop.

Projection version and contract fingerprint identify the serialized meaning. Solr index/schema identities, embedding serialization metadata, Qdrant sparse analyzer identity, and incremental state have their own compatibility checks. On incompatible persisted identity, indexing or retrieval fails explicitly until the operator chooses a compatible rebuild or migration. A file or package rename alone is not a reason to change these identities.

Projection v3 now derives `route_methods` and `route_paths` from validated
`api_contract/api_operation@1` and `api_contract/api_request@1` facts as well as
canonical behavior routes. This makes source-proved OpenAPI operations and
Postman requests available to exact route lookup without changing canonical
evidence. The projection contract revision is 4; its fingerprint is
`sha256:80d19b9400141cd7bb73f7ba7f64de414cd5a896b87c2dc1713959804696cef2`.
Snapshots with the earlier revision-3 fingerprint require an explicit rebuild.
Route lookup alone can be ambiguous when several methods or requests share a
path; the retrieval result does not grant a unique operation identity in that
case.

The Projection v3 reader validates persisted canonical payload types before reconstructing a `KnowledgeDocument`. Required strings and JSON integers, optional strings, string lists, provenance, and evidence representations must retain their canonical types; incompatible values raise `ProjectionParsingError`. Version and fingerprint mismatches raise `ProjectionCompatibilityError`. The historical `SolrCanonicalDocumentReader` alias exposes those same Projection errors; retrieval-facing mappers translate them to `KnowledgeSearchError`.

Adapters translate projected fields to vendor schemas and preserve provenance when materializing a hit. See [ADR-0004](../adr/ADR-0004-versioned-persisted-identities.md) and [namespace migration](../migrations/fetchcore-namespace.md).
