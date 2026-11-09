# Canonical Repository Knowledge

Repository sources enter through source materialization, safe scanning, corpus and document policy, then `RepositoryIndexer` extraction. The canonical unit is `KnowledgeDocument` in `repository/indexing.py`, with typed `KnowledgeEvidence`, a `KnowledgeProvenance` source trace, repository, revision, path, line span, and checksum. Source-derived relationships and KnowledgePacks enrich that knowledge without giving any source automatic authority.

`repository/knowledge.py` defines repository-owned knowledge manifests and refresh contracts; source policy is distinct from deployment runtime configuration. The indexer also records incremental state and fingerprints so unchanged compatible content can be reused safely.

Canonical documents are independent of Solr, OpenSearch, and Qdrant schemas. The adapter receives a projection derived from this knowledge. Vendor fields can change or an index can be rebuilt without redefining the source document. See [projection](projection.md), [evidence and provenance](../concepts/evidence-and-provenance.md), and [ADR-0001](../adr/ADR-0001-backend-neutral-knowledge.md).

## Initial structural navigation coverage

The default structural extractor registry includes Python, JavaScript, TypeScript, Java, Groovy, Dart, and Go. Go contributes observed package names, imports, type declarations, free functions, and receiver methods to the same canonical symbol and structure evidence as existing extractors. This is declaration/import coverage only: Go call targets, cross-file references, and inheritance/resolution are not inferred.

With the optional `structure` extra, Rust, C#, Kotlin, Ruby, PHP, Swift and Scala
contribute grammar-backed declarations and syntax-observed calls. Rust, C#,
Kotlin, PHP, Swift and Scala also contribute imports; Ruby currently has no
import claim. Calls retain `syntax` resolution and do not prove a target file or
cross-file binding. The dedicated CI job installs the extra and requires all
seven grammars, Go, and the six preexisting language extractors. Changing the
grammar or adapter contract requires explicit reindexing through the indexing
fingerprint.

`RepositoryKnowledgeNavigationService` performs exact name or qualified-name lookups over selected canonical `KnowledgeDocument` values. Operational stores select these values through persisted exact name, qualified-name, identity, or source-ref fields built at snapshot publication. They hydrate only bounded candidates and check the snapshot identity before returning. These fields are rebuildable projections; they do not replace canonical knowledge. It returns homonyms as separate candidates and carries source path, span, revision, checksum, and document identity. Related lookups expose only relations already attributed to that symbol; unresolved observations remain unresolved. Text-only files and languages without structural declarations are not reported as definitive symbol misses.

`RetrievalApplicationService` and the registered-repository serving service expose
the same operations through typed requests. They obtain canonical evidence from
a compatible persisted snapshot and its derived exact lookup keys, applying the
authorized repository scope. CLI and MCP only translate requests/results. No
dense fallback or graph hop is involved. Application results
report coverage and scan bounds independently of whether candidates were returned;
see the [navigation reference](../reference/cli-mcp.md#exact-structural-navigation).

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
`cross_file_resolution` only when implemented. The import-bound corpus resolver
adds that capability for Python and JavaScript/TypeScript during explicit indexing;
it resolves calls and proven test targets only when an import identifies one
repository-local file and one declaration. Dynamic or ambiguous targets remain
unresolved. An unregistered/undeclared producer
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
preserved when the target is inside the authorized serving scope; target location
fields outside that scope are withheld. Target revision/checksum are attached
only when that exact document exists in the same canonical snapshot. No target
is guessed by matching names.
Distinct evidence locations, resolution states and target provenance are kept.
Traversal is one hop, bounded to 1-100 results. `incoming` accepts a canonical
symbol identity and reads only the reverse exact-key index derived from resolved
target identities. It does not infer `called_by` from a textual name. The source
line, source identity, target identity, checksum and resolution evidence remain
attached. Removing or changing a target recomputes dependent edges during
explicit indexing, including the Git delta path. Queries never rebuild them.

The stored evidence contract uses indexing contract 29 and Projection v3
contract revision 5; older snapshots require an explicit rebuild.
Legacy evidence can still hydrate with missing declarations/capabilities, but is
not certified as current structural coverage. The embedding serializer is
unchanged. Derived exact lookup keys (names, qualified names and logical
identities and resolved incoming identities) are computed from canonical declarations
and relations during indexing (structural lookup layout v2) and are
bound to the indexing fingerprint. They are rebuildable adapter fields, not
canonical authority or new Projection v3 fields.

## Optional grammar declarations

The `structure` extra enables grammar-backed declarations and syntax-observed
calls for Rust, C#, Kotlin, Ruby, PHP, Swift and Scala. Rust, C#, Kotlin, PHP,
Swift and Scala also expose observed import targets; Ruby has no import claim.
Calls retain unresolved syntax status. These adapters do not claim references,
inheritance or cross-file resolution. Grammar package and adapter contract
versions participate in the indexing fingerprint; changed extraction requires
an explicit snapshot rebuild. Go keeps its declaration/import extractor.
