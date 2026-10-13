# Bounded local reference resolution — S5

**Status: opt-in.** `ApiContractRepositoryIndexer(...,
resolve_local_references=True)` runs `LocalReferenceResolver` after sanitized
source interpretation and evidence enrichment. The resolver sees only canonical
`KnowledgeDocument` evidence. It does not open files, follow links, call a
service, execute code, or use similarity. It changes only evidence; the
`KnowledgeCorpusResolver` boundary validates corpus membership, ordering,
identity, provenance, content and classification.

Transient exact indexes use `(repository, revision, path)`, API pointer,
FetchCore fragment ID, and `(repository, revision, HTTP method, route)`. The
resolver follows no references from a resolved target: its maximum expansion
depth and hop count are both one, so cycles do not expand work. Defaults cap
documents at 100,000, aggregate serialized evidence at 16,000,000 bytes,
examined references at 10,000, candidates per reference at 32, and emitted
positive relations at 10,000. The resolver examines a deterministic source
prefix of at most `max_references`, sorted by repository, revision, path,
start line, and canonical document ID. Output document order remains unchanged.
Later declarations retain their original
source observations and are counted exactly, without candidate lookup or an
individual resolution observation. One `local_reference/coverage@1` envelope
per nonempty pass records `examined`, `resolved`, `inconclusive`,
`omitted_by_budget`, `resolution_complete`, and `scope=corpus`. This is a pass-level diagnostic,
not a claim that a partial pass resolved the whole corpus. Candidate or relation
caps yield `truncated`; document or evidence-size caps raise a closed error
before resolution. Budget values enter the resolver indexing fingerprint.

Each examined source declaration produces
`local_reference/resolution@2` semantic evidence with kind, declaration,
source locator, status, evidence basis, candidate count, and proven target
identity/checksum/locator when available. Statuses are `resolved`,
`unresolved`, `ambiguous`, `truncated`, `unsupported`, and `blocked`. The source
extractors already preserve `observed` declarations. Only `resolved` produces
a `SemanticRelationEvidence`, with kind `api_reference`,
`markdown_reference`, or `route_correspondence`. Negative and inconclusive
statuses are diagnostics, not positive links. Source repository, revision,
path, checksum and provenance remain on the owning document; a target checksum
is present only for an exact target document. A resolved observation has an
explicit `target_scope`: `document` requires document ID, source ref, path,
revision, and checksum; `file` is allowed only for a Markdown path-only link,
requires path and revision, and makes no chunk, checksum, fragment, or symbol
claim. An inconclusive observation cannot name a target. The owning document
supplies the repository scope for file identity.

OpenAPI/Swagger `#/components/schemas/...` and `#/definitions/...` resolve
only to extracted `api_schema` evidence in the same repository and revision.
S5 also admits conservatively sanitized relative `$ref` declarations at the
OpenAPI sanitizer boundary. It normalizes them to a repository-relative target
path before creating the existing `api_contract/api_reference@1` fact. Paths
that escape the repository, remote URLs, invalid pointers and sensitive names
are omitted by the sanitizer, never raw-fallbacked. The raw spelling of a
relative declaration is not persisted; its source JSON Pointer is preserved.
RFC 6901 escaping is matched exactly. A syntactically valid pointer does not
prove a schema exists when no typed schema was extracted.

Markdown links resolve to admitted canonical paths. A path-only link proves
file presence, but leaves target document ID empty when the file has multiple
chunks. Section links require an exact FetchCore `fragment_id`; renderer slugs
are never inferred. A file can therefore be present while a fragment remains
unresolved. HTTP(S) links are `blocked`. Normalization rejects root escape and
unsafe absolute/backslash paths. No symlink is traversed because resolution
uses only safely admitted corpus documents.

Contract-to-code joining requires an exact HTTP method and path from an
`api_operation` and a structured, source-observed FastAPI `@app` route whose
receiver is proved by one module-level `app = FastAPI()` assignment or a
direct `FastAPI` import alias. The proof rejects reassignments, nested
shadowing, dynamic factories, computed paths, and unknown receivers. Such
syntax remains observed and an exact contract join is `unsupported` with
`unproven_framework_receiver`; no positive relation is emitted. `@router`
declarations are inconclusive because unresolved inclusion prefixes may change
their effective path. Multiple eligible route declarations yield `ambiguous`,
even when they occur in one file. The relation kind is
`route_correspondence`; it does not assert implementation correctness,
satisfaction, or authority. Other framework declarations remain unjoined until
their effective method/path can be proved under a separate bounded rule.

Resolution runs against the current full corpus before the canonical diff.
Removing or changing a target removes the old positive relation. Active
semantic extensions still require full scans on changed paths; S5 adds no
delta-resolution claim. The OpenAPI interpreter identity changes from version
1 to 2 for relative-reference acquisition; enabling the resolver also changes
the indexing fingerprint. Those affected persisted corpora require an explicit
rebuild. The S5 correction bumps the Python structural extractor identity to
version 2 and resolver identity to version 2, while leaving the global indexing
contract at version 26. Existing canonical snapshots with old Python route
evidence or `local_reference/resolution@1` require an explicit rebuild; old
resolution envelopes are not silently treated as version 2. Projection v3
fields and fingerprint remain unchanged; its existing `canonical_payload` and
relation fields carry the new evidence.

See the [S5 construction benchmark](../../benchmarks/local-reference-s5/README.md)
for the frozen corpus, ground truth, paired measurements and limits.
