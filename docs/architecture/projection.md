# Projection

`projection/core.py` compiles canonical `KnowledgeDocument` content into `ProjectedKnowledgeRecord` and reads the persisted representation. Retrieval owns matching and query semantics over those records in `retrieval/projection_matching.py`. `projection/semantics.py` and `projection_semantics.py` are historical import aliases to that retrieval module. Search and vector stores hold rebuildable projections, while canonical Repository Knowledge retains the source trace. A graph projection is a possible future addition, not a required current hop.

Projection version and contract fingerprint identify the serialized meaning. Solr index/schema identities, embedding serialization metadata, Qdrant sparse analyzer identity, and incremental state have their own compatibility checks. On incompatible persisted identity, indexing or retrieval fails explicitly until the operator chooses a compatible rebuild or migration. A file or package rename alone is not a reason to change these identities.

Projection v3 now derives `route_methods` and `route_paths` from validated
`api_contract/api_operation@1` and `api_contract/api_request@1` facts as well as
canonical behavior routes. This makes source-proved OpenAPI operations and
Postman requests available to exact route lookup without changing canonical
evidence. The current projection contract revision is 5; its fingerprint is
`sha256:4390897bae3636a7d79fb5318e80d79358053c8e4de0cfb6f295b884fd4c7a20`.
Revision 5 adds canonical declaration identities and extraction capabilities
inside `canonical_payload`; backend field layout and lexical ranking are unchanged.
Snapshots with earlier fingerprints require an explicit rebuild.
Route lookup alone can be ambiguous when several methods or requests share a
path; the retrieval result does not grant a unique operation identity in that
case.

The embedding serializer remains `projection-v3` version 2 with fingerprint
`sha256:5f859289b8b3a594e924545edb1c3ae6178b77ea22cf3ff224d43c5ec7aeb43a`:
the new provenance fields do not change its vector input. Rebuild/admission checks
still reject incompatible stored projection or indexing fingerprints.

The Projection v3 reader validates persisted canonical payload types before reconstructing a `KnowledgeDocument`. Required strings and JSON integers, optional strings, string lists, provenance, and evidence representations must retain their canonical types; incompatible values raise `ProjectionParsingError`. Version and fingerprint mismatches raise `ProjectionCompatibilityError`. The historical `SolrCanonicalDocumentReader` alias exposes those same Projection errors; retrieval-facing mappers translate them to `KnowledgeSearchError`.

Adapters translate projected fields to vendor schemas and preserve provenance when materializing a hit. See [ADR-0004](../adr/ADR-0004-versioned-persisted-identities.md) and [namespace migration](../migrations/fetchcore-namespace.md).
