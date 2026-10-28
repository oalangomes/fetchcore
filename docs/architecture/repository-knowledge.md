# Canonical Repository Knowledge

Repository sources enter through source materialization, safe scanning, corpus and document policy, then `RepositoryIndexer` extraction. The canonical unit is `KnowledgeDocument` in `repository/indexing.py`, with typed `KnowledgeEvidence`, a `KnowledgeProvenance` source trace, repository, revision, path, line span, and checksum. Source-derived relationships and KnowledgePacks enrich that knowledge without giving any source automatic authority.

`repository/knowledge.py` defines repository-owned knowledge manifests and refresh contracts; source policy is distinct from deployment runtime configuration. The indexer also records incremental state and fingerprints so unchanged compatible content can be reused safely.

Canonical documents are independent of Solr, OpenSearch, and Qdrant schemas. The adapter receives a projection derived from this knowledge. Vendor fields can change or an index can be rebuilt without redefining the source document. See [projection](projection.md), [evidence and provenance](../concepts/evidence-and-provenance.md), and [ADR-0001](../adr/ADR-0001-backend-neutral-knowledge.md).

## Initial structural navigation coverage

The default structural extractor registry includes Python, JavaScript, TypeScript, Java, Groovy, Dart, and Go. Go contributes observed package names, imports, type declarations, free functions, and receiver methods to the same canonical symbol and structure evidence as existing extractors. This is declaration/import coverage only: Go call targets, cross-file references, and inheritance/resolution are not inferred.

`RepositoryKnowledgeNavigationService` performs exact name or qualified-name lookups over a single repository revision's canonical `KnowledgeDocument` values. It returns homonyms as separate candidates and carries source path, span, revision, checksum, and document identity. Related lookups expose only relations already attributed to that symbol; unresolved observations remain unresolved. The service does not construct an index or load documents from a retrieval backend, so callers must supply the canonical document snapshot they intend to navigate. Text-only files and languages without structural declarations are not reported as definitive symbol misses.

## Canonical declaration and relation contracts

`KnowledgeEvidence.declarations` preserves all observed declarations overlapping a
chunk, not only its primary symbol. Each existing `SymbolEvidence` gains a typed
`DeclarationIdentity`: repository, path, language, kind and qualified name. Its
`symbol:v1:sha256:` key excludes revision, checksum, span and signature for unique
declarations. Only colliding declarations use a signature discriminator; repeated
equal signatures also use a source-order occurrence. Adding/removing an overload
can therefore change the discriminator. An indistinguishable redefinition's
occurrence is not a permanent semantic identifier across insertions.

`parent_identity` links an observed member to a unique observed same-file
container. Otherwise it remains absent; textual `container` evidence is retained.
The source reference is the file-level parent. Span, revision and source checksum
remain separate provenance, including when a member lies outside its type's span
(Go receiver methods). Chunk replicas deduplicate by identity and revision, with
stable source/document ordering, while homonyms and overloads remain candidates.
Renames change identity; line movement, body edits and unique-signature edits do
not. A new snapshot replaces the derived view, so deleted declarations and
relations disappear. Stale source checksums/revisions cannot be used for navigation.

`KnowledgeEvidence.extraction` persists the language, producer identity/version and
verified fact capabilities. Producers declare `declarations`, `imports`,
`calls_observed`, `references_observed`, `tests_observed`, `inheritance`, or
`cross_file_resolution` only when implemented. An unregistered/undeclared producer
is `source_only`; legacy documents retain unknown coverage, not invented metadata.
New adapters normalize into these same contracts before projection; no parser,
SCIP index, tags tool or graph database is required.

The SDK service exposes `capabilities()` and `coverage()` for a language/path.
Coverage is `supported`, `partial`, `unsupported` or `unknown`, counting distinct
sources rather than chunks. An empty lookup means a miss only within this observed
coverage. A source-only scope raises `StructuralNavigationError` with
`unsupported_capability` for symbol lookup; unsupported relation families or
required precise resolution also fail explicitly. Empty corpora have unknown
coverage. Current extractor capabilities are bounded observations, not promises
of universal syntax coverage or repository completeness.

`get_related` takes a selected `SymbolDefinition` or a canonical file `source_ref`.
It normalizes symbol-owned calls/references/tests to `calls`, `references` and
`tests`, retaining the producer's subtype and resolution vocabulary. File-owned
imports, dependencies and semantic relations are not falsely attributed to every
symbol in that file. `RelatedEvidence.status` is separately `observed`, `resolved`,
`unresolved`, `ambiguous` or `unsupported`: syntax/import bindings do not prove a
cross-file definition. Exact resolver-supplied target IDs/references/paths are
preserved; target revision/checksum are attached only when that exact document
exists in the same canonical snapshot. No target is guessed by matching names.
Distinct evidence locations, resolution states and target provenance are kept.
Traversal is outgoing, one hop, bounded to 1-100 results. Reverse traversal is
explicitly unavailable, not approximated by a name search.

This delivery changes the stored evidence contract: indexing contract 28 and
Projection v3 contract revision 5 require an explicit rebuild of older snapshots.
Legacy evidence can still hydrate with missing declarations/capabilities, but is
not certified as current structural coverage. The embedding serializer is
unchanged. CLI/MCP wiring remains separate work in #27; this is the canonical
contract and SDK boundary, not a new retrieval algorithm.
