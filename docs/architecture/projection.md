# Projection

`projection/core.py` compiles canonical `KnowledgeDocument` content into `ProjectedKnowledgeRecord`; `projection/semantics.py` owns shared match and query semantics. Search and vector stores hold rebuildable projections, while canonical Repository Knowledge retains the source trace. A graph projection is a possible future addition, not a required current hop.

Projection version and contract fingerprint identify the serialized meaning. Solr index/schema identities, embedding serialization metadata, Qdrant sparse analyzer identity, and incremental state have their own compatibility checks. On incompatible persisted identity, indexing or retrieval fails explicitly until the operator chooses a compatible rebuild or migration. A file or package rename alone is not a reason to change these identities.

The Projection v3 reader validates persisted canonical payload types before reconstructing a `KnowledgeDocument`. Required strings and JSON integers, optional strings, string lists, provenance, and evidence representations must retain their canonical types; incompatible values raise `ProjectionParsingError`. Version and fingerprint mismatches raise `ProjectionCompatibilityError`. The historical `SolrCanonicalDocumentReader` alias exposes those same Projection errors; retrieval-facing mappers translate them to `KnowledgeSearchError`.

Adapters translate projected fields to vendor schemas and preserve provenance when materializing a hit. See [ADR-0004](../adr/ADR-0004-versioned-persisted-identities.md) and [namespace migration](../migrations/fetchcore-namespace.md).
