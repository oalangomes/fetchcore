# Current contracts

**Status: current Python contracts.** Import paths shown below are the owning modules; compatibility imports may also exist. These are acquisition and serialization contracts, not consumer approval rules.

| Contract | Module | Current meaning |
| --- | --- | --- |
| `QuerySignal` | `fetchcore.retrieval.signals` | Typed query signal with classification, origin, weight, and optional `required` marker. |
| `SearchIntent` | `fetchcore.retrieval.contracts` | Canonical query, anchors, declared expression, expansions, hints, signals and legacy terms; see [SearchIntent v2](../concepts/search-intent.md). |
| `Term`, `Anchor`, `AllOf`, `AnyOf`, `QueryExpansion` | `fetchcore.retrieval.contracts` | Immutable declared meaning and expansion provenance, with deterministic mapping. |
| `IntentTranslation` | `fetchcore.retrieval.translation` | Channel, translation status, reasons and unsupported constructs. |
| `SearchRequirement` | `fetchcore.retrieval.contracts` | Knowledge type, capabilities, minimum/target allocation and priority hint. |
| `SearchScope` | `fetchcore.retrieval.contracts` | Workspace, revision/repository, knowledge types, result bound, mode, paths, settings and optional `RetrievalConfig`. |
| `SearchResult` | `fetchcore.retrieval.contracts` | Hit identity, content, score, source trace, capabilities, observations, relationships, query matches. |
| `SearchRelationship` | `fetchcore.retrieval.contracts` | Normalized type, target, confidence, and discovery source for a hit. |
| `KnowledgeSearchResponse` | `fetchcore.retrieval.contracts` | Backend, status, results, metadata, duration and optional telemetry. |
| `KnowledgeSearchBackend` | `fetchcore.retrieval.contracts` | Port with `backend_id`, `retrieval_capabilities`, and `search(intent, scope, requirements)`. |
| `ExactLookupBackend` | `fetchcore.retrieval.contracts` | Optional bounded exact-acquisition port negotiated through `targeted_lookup`; see [targeted retrieval](../concepts/targeted-retrieval.md). |
| `EvidenceObservation` | `fetchcore.retrieval.evidence` | Validated source-derived category/subtype/value and source trace. |
| `QueryMatchEvidence` | `fetchcore.retrieval.evidence` | Signal, match type, channels, values, source and bounded contribution details. |
| `KnowledgeEvidence`, `SectionEvidence`, `MarkdownReferenceEvidence` | `fetchcore.knowledge.evidence` | Canonical source facts; Markdown sections may include heading ancestry, FetchCore section/fragment locators, declared fence languages and safe link destinations marked as observed rather than resolved. |
| `SymbolEvidence` | `fetchcore.knowledge.evidence` | Observed declaration syntax/span with optional logical identity and parent identity; canonical `declarations` preserves members independently of chunk roots. |
| `DeclarationIdentity`, `ExtractionCapabilities`, `StructuralCoverage` | `fetchcore.knowledge.structure` | Revision-independent symbol keys, producer/version and verified capability vocabulary, and explicit supported/partial/unsupported/unknown source coverage. |
| `SymbolDefinition`, `RelatedEvidence` | `fetchcore.repository.navigation` | Serializable exact-navigation results with source provenance, directed relation kind/subtype, original resolution and normalized epistemic status. See [canonical structure](../architecture/repository-knowledge.md#canonical-declaration-and-relation-contracts). |
| `FindSymbolRequest`, `GetRelatedRequest`, `NavigationResult` | `fetchcore.application.navigation` | Shared SDK/CLI/MCP exact navigation requests and bounded results with capability coverage, scan diagnostics and explicit partial/unsupported status. See [navigation reference](cli-mcp.md#exact-structural-navigation). |
| `EmbeddingProvider` | `fetchcore.retrieval.embedding` | Provider ID and embedding operations independent of Ollama transport. |
| `RetrievalConfig` | `fetchcore.retrieval.config` | Targeted early-return policy, dense, hybrid, reranking and LTR settings; effective mode is lexical, dense or hybrid. |
| `RetrievalBackendCapabilities` | `fetchcore.retrieval.config` | Advertised lexical, dense, hybrid, LTR, direct canonical-query and lexical requirement support. The serialized reranking flag is retained for compatibility; reranking is post-acquisition. |

`SearchResult.to_mapping()` omits empty/absent fields and validates alignment score and capability source identity. `SearchScope.to_mapping()` excludes runtime-only `settings`. `KnowledgeSearchResponse.to_mapping()` includes telemetry when present. Consult the dataclasses and round-trip tests before changing serialized meaning. See [capabilities](capabilities.md) and [evidence vocabulary](../concepts/evidence-and-provenance.md).

Markdown section details and their explicit rebuild behavior are documented in
[Technical Markdown extraction S4](../architecture/markdown-evidence.md).
