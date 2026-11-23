# GraphProjection v1

FetchCore derives a separate, rebuildable structural projection from canonical
`KnowledgeDocument` evidence. It does not infer a link from text, names, scores,
or Solr responses. A graph record is one `SemanticRelationEvidence` with
`resolution` of `resolved` or `exact-unique`, an explicit target identity, and
a target document containing that declaration in the same repository revision.
The projector checks target source reference and path against the canonical
document. A missing target after removal or rename is excluded. Other
observations stay in canonical knowledge for `get_related`.

The logical contract is `GraphEdge` and `GraphProjection` in
`src/fetchcore/knowledge/graph_projection.py`. Edge IDs hash the complete
observation, including repository, revision, document IDs, checksums, source
line, relation kind and target identity. Input order and identical duplicate
observations do not affect the serialized artifact. Different source lines or
evidence remain separate records. The projection fingerprint includes its
version, semantic revision, and edge fields; it is separate from Projection v3.

The SolrCloud adapter persists edge documents and one manifest in a distinct
physical collection using `fetchcore-graph-v1`. Its explicit schema has no
content body or embedding field. Indexed exact fields support source or target
adjacency queries; `canonical_payload` preserves the complete edge. Reads
verify the schema and every indexed field against the payload and reject
incompatible version, fingerprint or scope. The graph collection is a
projection, never a source of canonical truth.

## Extraction coverage at this boundary

The current canonical model can supply declarations, source references,
imports, observed calls/references/tests, and semantic relations resolved by
the existing extractor. Projectable links require both canonical identities
and exact target documents. File-owned import observations may use their
canonical source reference as source identity. Unresolved, ambiguous,
unsupported and merely observed relations are not navigable. Route,
persistence, event and test-flow maps are only as complete as the current
extractors and their exact resolution; GraphProjection does not add those
facts. `ExtractionCapabilities` and structural coverage must accompany map
responses so missing extraction is distinguishable from a verified absence.

## M1 verification

`tests/unit/knowledge/test_graph_projection.py` covers input order, parallel
observations, duplicate observations, revision and checksum changes, source
line shifts, repository identity, cycles, homonyms, target rename and
incompatible fingerprints. `tests/integration/test_graph_store_live.py`
creates and cleans up a uniquely named two-shard collection on an explicitly
disposable SolrCloud, verifies the packaged schema, rejects a changed field
type, and round-trips a three-edge fixture including two call sites between
the same symbols. It requires `FETCHCORE_GRAPH_TEST_SOLR_URL` and a preloaded
`fetchcore-graph-v1` configset; it is skipped otherwise.

## Publication and serving

`GraphPublicationService` stages a complete physical graph collection while
holding the existing knowledge writer lock. Its versioned manifest binds the
exact physical knowledge generation and snapshot identity. Only one graph
alias changes at activation. The reader pins physical names, checks graph and
knowledge aliases and manifest before and after reading, and rejects an absent,
stale or incompatible graph. A knowledge-only sync leaves ordinary retrieval
working and causes graph navigation to fail closed until an explicit graph
rebuild. See [ADR-0007](../adr/ADR-0007-bounded-graph-projection-publication.md).

`index graph-rebuild REPOSITORY` is the explicit full rebuild. It re-extracts
canonical knowledge from the registered source and publishes only if its
revision, corpus checksum and indexing fingerprint equal the active knowledge
snapshot. `index graph-status REPOSITORY` is read-only. Staging failures retain
the previous alias; unconfirmed activation and orphan locks require operator
inspection. The publisher does not automatically delete retained generations.

## Bounded knowledge map

`navigate_graph` in the application boundary accepts an exact canonical symbol
seed, optional exact target, direction, relation types, depth (one to three)
and explicit budgets. It
executes bounded exact adjacency queries, then checks each edge against
canonical source and target documents. The response contains entities, edges,
observation-specific paths, inconclusive observations, extraction coverage,
consumed budgets, diagnostics and generation identity. SDK, CLI and MCP expose
the same mapping; `get_related` remains the independent one-hop operation.
The map is evidence of static source relations, not proof of runtime execution
or consumer satisfaction.

The M0 `nodes()`/`shortestPath()` spike did not prove a strict server-side
intermediate-node ceiling. The v1 serving decision is therefore exact BFS by
hop: each Solr query uses repository, revision, generation, endpoint and
relation filters, `rows=max_remaining_edges+1`, and the application caps
expansions, nodes, edges, paths, depth and output bytes. A high-degree node can
mark the map partial without processing the remainder. No public Streaming
Expression is exposed. The operation deadline includes preflight, traversal,
hydration and final generation checks; an expired deadline is a typed timeout.
Solr `timeAllowed` rejects partial responses. Traversal can return a partial
bounded map when a node/edge/path/expansion ceiling is met, with exact edges
already examined and an explicit diagnostic. This choice pays more backend
round trips than a single streaming traversal in exchange for a provable
fan-out ceiling. The [ADR](../adr/ADR-0007-bounded-graph-projection-publication.md)
records that trade-off.

For recovery, `index graph-status` inspects the manifest and retained physical
generations without mutation. If graph publication fails before activation,
the previous graph alias remains in place; serving checks its binding against
the current knowledge alias and may still reject it. An uncertain activation,
changed alias, orphan writer lock or missing graph generation requires operator
inspection with existing SolrCloud reconciliation commands and an explicit
`index graph-rebuild` once knowledge is stable. No generation is deleted or
reset automatically. A newly indexed knowledge generation makes old graph
navigation unavailable until its compatible graph is rebuilt. Search and
`get_related` remain independent of graph readiness.
