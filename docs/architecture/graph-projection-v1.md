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
