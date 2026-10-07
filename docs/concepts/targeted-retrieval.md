# Targeted exact retrieval

`RetrievalApplicationService.search(SearchRequest(intent=...))` attempts bounded
exact acquisition for explicit top-level `SearchIntent.anchors` of kind `path` or
`source_ref`. MCP's `search` tool accepts the same serialized `intent`; it does
not implement retrieval policy. Existing `query` callers remain unchanged. Do
not combine `intent` with the legacy `query`/`exact_terms` convenience fields.

```python
from fetchcore.application import SearchRequest
from fetchcore.retrieval.contracts import Anchor, SearchIntent

response = service.search(SearchRequest(intent=SearchIntent(
    canonical_query="show the product service",
    anchors=(Anchor("path", "src/product/service.py", origin="caller"),),
)))
```

A file-level reference uses `Anchor("source_ref",
"repository://owner/repo/src/product/service.py")`. References use the canonical
repository URI and percent encoding. Relative references, fragments, traversal,
noncanonical paths and symlinks are rejected. No path is inferred from text,
hints, expansions or signals. Nested expression anchors keep their existing
translator behavior. Repository paths are exact allowed paths, not new glob
syntax.

## Policy and determinism

`RetrievalConfig.targeted` accepts `exact_unique` (default) or `disabled`. The
policy is independent from the configured lexical/dense/hybrid mode. It governs
only application-service calls; direct `backend.search` retains its behavior.
The default is omitted from configuration mappings, so existing serialized
configuration/fingerprints remain byte-for-byte compatible. Explicit `disabled`
round-trips as an additive field. No index or embedding rebuild is required.

Early return requires exactly one supported anchor, no additional required
anchors/boolean expression, a complete probe, and exactly one record matching
the canonical source identity. Text relevance, scores and answer quality never
participate in this decision. Optional ranking text may therefore be bypassed
on exact acquisition. Additional required constructs always reach the existing
translation checks. FetchCore retrieves evidence; consumers decide satisfaction
and authority.

Outcomes are `exact_unique`, `exact_ambiguous`, `not_found` and `unsupported`.
Disabled policy bypasses exact acquisition and sends the original intent through
normal translation validation and backend search. Additional required semantics
have a separate fallback reason. Errors in snapshots, revision, scope or backend protocol propagate;
they do not turn into misses or trigger fallback.

## Capability and adapters

`RetrievalBackendCapabilities.targeted_lookup` negotiates the optional
`ExactLookupBackend.lookup_exact(anchor, scope)` port. It returns ordinary
`KnowledgeSearchResponse`/`SearchResult`, at most two records and an explicit
`metadata.output_truncated` boolean. A backend declaring the capability without
implementing the port is a contract error. The existing broad search port has
not changed.

| Adapter | Exact acquisition |
| --- | --- |
| Local | Direct safe read of one source; no corpus enumeration or lexical scoring. Existing include/exclude patterns, git-ignore admission and knowledge-type rules apply. |
| Solr | Existing exact keyword translation and snapshot validation, with a two-record probe and dense disabled for that probe. |
| OpenSearch | Existing exact keyword translation and snapshot validation, with a two-record probe and dense disabled for that probe. |
| Qdrant | Capability unavailable in this slice; normal retrieval receives the original intent and rejects unsupported hard semantics. |

Local revision-bound lookup verifies HEAD and compares the selected file's
byte checksum against the committed Git blob before attaching the revision.
A changed/untracked file cannot receive
a committed revision. Without a bound revision, current working-tree bytes have
an explicitly absent revision. Local content retains existing redaction and
source-checksum semantics; `document_checksum` identifies the complete source.
Content is bounded by serving byte/line limits (defaults 1 MB/400 lines), with
`content_truncated` when a prefix is returned. This is no claim that the prefix
contains all evidence a consumer needs.

Projection adapters conservatively treat multiple records/chunks of the same
source as ambiguous. This avoids hiding unreturned chunks behind a uniqueness
claim. They preserve existing IDs, source references, revision, checksums,
relationships, observations and projection metadata. No projection identity or
canonical knowledge schema is changed.

## Explicit fallback

When lookup is unsupported, ambiguous or missing, normal retrieval receives the
**original SearchIntent** with every required anchor intact. The existing
translation policy checks the configured lexical, dense or hybrid channel.
If the channel cannot preserve a required anchor, it raises
`KnowledgeSearchError(category="unsupported_semantics")`. In particular, local
and dense-only fallback cannot enforce path/source-reference anchors. There is
no post-filter, adaptive budget, mode substitution or removal of scope.

## Evidence and telemetry

Exact results use the existing `QueryMatchEvidence` (`exact_structural`) and
`EvidenceObservation` vocabulary. No strength or confidence is synthesized.
Anchor origin is retained in its `QuerySignal`. Normal fallback preserves the
backend's own evidence without synthesizing a targeted match.

`response.metadata.targeted` and `response.telemetry.metadata.targeted` carry
the same additive, version-1 audit mapping:

- `targeted_attempted`, `targeted_signal_kind`, `targeted_hits`, `targeted_ms`;
- `outcome`, `fallback_used`, `fallback_reason`;
- `lookup_candidates_examined`, total `candidates_examined`;

A `targeted` stage precedes existing stages on fallback. Total duration/candidate
counts include both operations; returned counts/chars describe the backend
results. High-resolution `targeted_ms` is in milliseconds; existing integer
stage/response duration contracts remain unchanged. Backend response metadata
still describes raw backend acquisition where applicable. Queries without a
targetable anchor retain their existing serialized output. Runtime does not
expose time-to-first-candidate, so it is not synthesized.

See the [controlled experiment](../../benchmarks/targeted-exact/README.md) for
measured scope, raw observations and limitations. Identifier/symbol/route lookup,
Qdrant exact lookup, graph, routing, RRF tuning and reranking remain outside this
slice.
