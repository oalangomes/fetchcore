# Canonical Repository Knowledge

Repository sources enter through source materialization, safe scanning, corpus and document policy, then `RepositoryIndexer` extraction. The canonical unit is `KnowledgeDocument` in `repository/indexing.py`, with typed `KnowledgeEvidence`, a `KnowledgeProvenance` source trace, repository, revision, path, line span, and checksum. Source-derived relationships and KnowledgePacks enrich that knowledge without giving any source automatic authority.

`repository/knowledge.py` defines repository-owned knowledge manifests and refresh contracts; source policy is distinct from deployment runtime configuration. The indexer also records incremental state and fingerprints so unchanged compatible content can be reused safely.

Canonical documents are independent of Solr, OpenSearch, and Qdrant schemas. The adapter receives a projection derived from this knowledge. Vendor fields can change or an index can be rebuilt without redefining the source document. See [projection](projection.md), [evidence and provenance](../concepts/evidence-and-provenance.md), and [ADR-0001](../adr/ADR-0001-backend-neutral-knowledge.md).

## Initial structural navigation coverage

The default structural extractor registry includes Python, JavaScript, TypeScript, Java, Groovy, Dart, and Go. Go contributes observed package names, imports, type declarations, free functions, and receiver methods to the same canonical symbol and structure evidence as existing extractors. This is declaration/import coverage only: Go call targets, cross-file references, and inheritance/resolution are not inferred.

`RepositoryKnowledgeNavigationService` performs exact name or qualified-name lookups over a single repository revision's canonical `KnowledgeDocument` values. It returns homonyms as separate candidates and carries source path, span, revision, checksum, and document identity. Related lookups expose only relations already attributed to that symbol; unresolved observations remain unresolved. The service does not construct an index or load documents from a retrieval backend, so callers must supply the canonical document snapshot they intend to navigate. Text-only files and languages without structural declarations are not reported as definitive symbol misses.
