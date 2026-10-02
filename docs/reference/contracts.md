# Current contracts

**Status: current Python contracts.** Import paths shown below are the owning modules; compatibility imports may also exist. These are acquisition and serialization contracts, not consumer approval rules.

| Contract | Module | Current meaning |
| --- | --- | --- |
| `QuerySignal` | `fetchcore.retrieval.signals` | Typed query signal with classification, origin, weight, and optional `required` marker. |
| `SearchIntent` | `fetchcore.retrieval.contracts` | `exact_terms`, `semantic_terms`, `hints`, `signals`; `terms` deduplicates exact and semantic terms. |
| `SearchRequirement` | `fetchcore.retrieval.contracts` | Knowledge type, capabilities, minimum/target allocation and priority hint. |
| `SearchScope` | `fetchcore.retrieval.contracts` | Workspace, revision/repository, knowledge types, result bound, mode, paths, settings and optional `RetrievalConfig`. |
| `SearchResult` | `fetchcore.retrieval.contracts` | Hit identity, content, score, source trace, capabilities, observations, relationships, query matches. |
| `SearchRelationship` | `fetchcore.retrieval.contracts` | Normalized type, target, confidence, and discovery source for a hit. |
| `KnowledgeSearchResponse` | `fetchcore.retrieval.contracts` | Backend, status, results, metadata, duration and optional telemetry. |
| `KnowledgeSearchBackend` | `fetchcore.retrieval.contracts` | Port with `backend_id`, `retrieval_capabilities`, and `search(intent, scope, requirements)`. |
| `EvidenceObservation` | `fetchcore.retrieval.evidence` | Validated source-derived category/subtype/value and source trace. |
| `QueryMatchEvidence` | `fetchcore.retrieval.evidence` | Signal, match type, channels, values, source and bounded contribution details. |
| `EmbeddingProvider` | `fetchcore.retrieval.embedding` | Provider ID and embedding operations independent of Ollama transport. |
| `RetrievalConfig` | `fetchcore.retrieval.config` | Dense, hybrid, reranking and LTR settings; effective mode is lexical, dense or hybrid. |
| `RetrievalBackendCapabilities` | `fetchcore.retrieval.config` | Advertised lexical, dense, hybrid and LTR acquisition support. The serialized reranking flag is retained for compatibility; reranking is post-acquisition. |

`SearchResult.to_mapping()` omits empty/absent fields and validates alignment score and capability source identity. `SearchScope.to_mapping()` excludes runtime-only `settings`. `KnowledgeSearchResponse.to_mapping()` includes telemetry when present. Consult the dataclasses and round-trip tests before changing serialized meaning. See [capabilities](capabilities.md) and [evidence vocabulary](../concepts/evidence-and-provenance.md).
