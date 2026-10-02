# Evidence and provenance

`KnowledgeDocument` carries source-derived evidence and a trace back to the repository revision, path, line range, and checksum. A retrieved `SearchResult` can carry that trace plus observations, query matches, and relationships. Missing provenance remains missing; it must not be invented during fusion or packaging.

`EvidenceObservation` records a validated source fact such as an identifier, path, route, reference, or dependency. `QueryMatchEvidence` records how a `QuerySignal` matched through named retrieval channels. Neither grants authority, proves answer correctness, or decides whether a consumer has enough context. See the precise fields in [contracts](../reference/contracts.md).
