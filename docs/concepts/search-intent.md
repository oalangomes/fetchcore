# Search intent and scope

FetchCore preserves structured retrieval intent; capable backends remain responsible for retrieval mechanics. Retrieval never decides satisfaction or authority.

`SearchIntent` represents meaning. `SearchScope` supplies authorized repository, revision, paths, knowledge types and result bounds. `RetrievalConfig` selects execution capabilities. Neither scope nor execution configuration belongs in the intent.

## SearchIntent v2

The immutable contract lives in `fetchcore.retrieval.contracts`:

```text
SearchIntent
├── canonical_query: str                    original consumer text
├── anchors: tuple[Anchor, ...]             required structured facts, ANDed
├── expression: SearchExpression | None     optional required declared tree
├── expansions: tuple[QueryExpansion, ...]  optional recall additions with origin
├── hints: tuple[str, ...]                  weak preferences
├── signals: tuple[QuerySignal, ...]        existing typed query evidence
├── exact_terms: tuple[str, ...]            v1 compatibility
└── semantic_terms: tuple[str, ...]          v1 compatibility

SearchExpression = Term | Anchor | AllOf | AnyOf
```

`Anchor(kind, value, origin="caller")` supports `identifier`, `qualified_symbol`, `path`, `route` and `source_ref`. Anchors are required facts, not estimates of relevance. `Term(value)` is a declared lexical phrase. `AllOf(children)` requires all children; `AnyOf(children)` requires at least one child. Grouping applies within one retrieved record, not across an arbitrary set of results. Children are nonempty tuples; intent structure is limited to depth 16 (root depth zero) and 256 nodes. `QueryExpansion(value, origin)` records explicit recall text and who supplied it.

Top-level anchors and the expression constrain the base query. Explicit expansions add recall alternatives to that base without relaxing anchors or branches. Hints remain preferences. A requirements-only intent starts with all records satisfying those requirements, so adding an expansion cannot narrow it.

```python
from fetchcore.retrieval.contracts import AllOf, Anchor, AnyOf, SearchIntent, Term

intent = SearchIntent(
    canonical_query="checkout implementation",
    expression=AnyOf((
        AllOf((Anchor("identifier", "ProductService"), Term("checkout"))),
        AllOf((Anchor("identifier", "ProductFacade"), Term("cart"))),
    )),
)
restored = SearchIntent.from_mapping(intent.to_mapping())
assert restored == intent
```

The service passes `SearchRequest.query` unchanged as `canonical_query`, including whitespace. It uses stripped text only to validate that the request is nonempty. Explicit `exact_terms` keep their existing whitespace normalization. No anchors, boolean branches or synonyms are inferred from a natural-language sentence. Structured callers use the backend-neutral SDK contract directly; the MCP/CLI request shape is unchanged.

## Compatibility and versioning

V1 maps directly to the same four fields: `exact_terms`, `semantic_terms`, `hints`, `signals`. No v1 exact term becomes a required anchor; no v1 semantic value becomes the canonical query. In particular, `QuerySignal.required` retains its existing adapter weighting meaning and is not migrated into a hard filter. Callers that need a hard requirement must declare an `Anchor` or expression.

Legacy positional constructors and `terms` (the deduplicated exact/semantic compatibility view) remain supported. The flat `terms` property deliberately does not expose or flatten v2 structure.

Intents using v2 fields serialize with `schema_version: 2`. Legacy-only intents retain their exact unversioned v1 mapping, preserving frozen benchmark fingerprints. The reader accepts unversioned/v1 or v2 mappings and rejects unknown versions, fields, types and malformed structures. New fields require an explicit v2 version. An empty or legacy-only v2 envelope normalizes back to the legacy mapping; it does not preserve a redundant envelope version.

Document projection, serializer identities and document embedding bytes are unchanged. A canonical-only dense query is sent to the encoder verbatim. When canonical text is present, dense consumes it alone; optional expansions, hints, signals and legacy terms are reported as unconsumed. Legacy-only intents retain their byte-identical grouped serialization. There is no document rebuild or re-embedding requirement for this change. A query cache must, as before, include the actual encoder input and embedding identity.

Third-party backends must advertise `canonical_query=True` and consume that field before receiving serving queries; the application rejects incapable backends instead of silently sending an empty legacy query. No generic targeted router is introduced.

## Translation and diagnostics

The unsupported **required** semantics policy is **reject**. There is no configurable flattening fallback. Adapters check participating channels before backend I/O, scanning or embedding. Canonical text requires canonical support; anchors and expressions require effective channel support. Optional expansions, hints and signals may be unconsumed without rejecting acquisition. Hybrid still rejects hard constraints that its current fusion pipeline cannot guarantee. Rejection raises `KnowledgeSearchError(category="unsupported_semantics")` with the channel, unsupported constructs, reason and `translation_policy: reject`.

The small `IntentTranslation` observation distinguishes:

- `preserved`: mandatory semantics are preserved and supplied optional semantics are consumed by this channel. Native canonical analysis and caller-declared expansions are preserved translations.
- `partially_unsupported`: required semantics are preserved but optional semantics were not consumed. Dense with canonical text plus expansions/hints is valid and uses this status. Local reference-only signals can also produce it without changing the v1 matcher.
- `rejected`: required semantics cannot be guaranteed. Unsupported canonical text, anchors or expressions fail before acquisition.
- `broadened`: translation deliberately relaxes declared meaning or structure. No current successful path emits it; a caller-supplied expansion alone is not broadening.

`unsupported` remains a compatibility view; `unsupported_required` and `unsupported_optional` distinguish rejection from optional omissions. Rejected structure is never concatenated into embedding text.

When `settings.diagnostic_candidate_window` is enabled, response metadata includes `intent_translation` per channel: canonical text, actual lexical request/embedding text/local terms/sparse terms, canonical consumption, consumed anchors and expression, supplied/consumed legacy signals and hints, supplied expansions (the existing `expansions` field) and `expansions_consumed` with origins, unsupported required/optional constructs, status and reasons. This diagnostic explicitly contains query text; leave it disabled to avoid the additional query payload. Failure metadata is available regardless of diagnostics. Existing telemetry, result provenance and acquisition scores retain their meanings.

| Backend/channel | Canonical query | Anchors | AnyOf / AllOf | Expansions | Unsupported behavior |
| --- | --- | --- | --- | --- | --- |
| Local lexical | Existing local phrase/term matcher; no new analyzer | Unsupported | Unsupported | Existing matching over explicit additional text | Reject required structure |
| Solr lexical | Native eDisMax using existing semantic fields/boosts | Required field filters | Nested OR/AND filters | Explicit optional recall clauses | Preserve or reject before execution |
| OpenSearch lexical | Native `multi_match` using existing semantic fields/boosts | Required field filters | Nested bool filters | Explicit optional recall clauses | Preserve or reject before execution |
| Solr/OpenSearch dense | Verbatim canonical encoder input | Unsupported | Unsupported | Unconsumed, reported | Reject required structure only |
| Qdrant dense | Same encoder contract | Unsupported | Unsupported | Unconsumed, reported | Reject required structure only |
| Qdrant hybrid | Canonical query to encoder and existing adapter sparse analyzer | Unsupported | Unsupported | Consumed by sparse, unconsumed by dense | Reject required structure only |

Solr/OpenSearch hybrid executes lexical canonical text plus supported optional additions alongside dense canonical text, then uses the existing fusion. It still rejects anchors and groups because dense candidates and post-fusion output cannot guarantee them. There is no post-fusion constraint engine. `structured_anchors` and `boolean_expressions` independently describe lexical support; dense does not inherit them. Expressions containing anchors require both capabilities. Supporting hard constraints after fusion remains a follow-up. Path/source-reference filters use exact source identity; identifier/symbol/route fields use the adapters' existing case normalization. Translation does not imply that a repository actually contains a matching identifier or a qualified symbol.

There is no generic NLP, natural-language boolean parser, core tokenizer/stemmer, synonym engine, learned planner, automatic expansion generation or targeted fast path. See [retrieval architecture](../architecture/retrieval.md), [capabilities](../reference/capabilities.md) and the [measured experiment](../benchmarks/search-intent-v2.md).
