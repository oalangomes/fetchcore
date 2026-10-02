# Canonical Repository Knowledge

Repository sources enter through source materialization, safe scanning, corpus and document policy, then `RepositoryIndexer` extraction. The canonical unit is `KnowledgeDocument` in `repository/indexing.py`, with typed `KnowledgeEvidence`, a `KnowledgeProvenance` source trace, repository, revision, path, line span, and checksum. Source-derived relationships and KnowledgePacks enrich that knowledge without giving any source automatic authority.

`repository/knowledge.py` defines repository-owned knowledge manifests and refresh contracts; source policy is distinct from deployment runtime configuration. The indexer also records incremental state and fingerprints so unchanged compatible content can be reused safely.

Canonical documents are independent of Solr, OpenSearch, and Qdrant schemas. The adapter receives a projection derived from this knowledge. Vendor fields can change or an index can be rebuilt without redefining the source document. See [projection](projection.md), [evidence and provenance](../concepts/evidence-and-provenance.md), and [ADR-0001](../adr/ADR-0001-backend-neutral-knowledge.md).
