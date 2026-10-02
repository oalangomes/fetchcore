# Retrieval contract extraction

Tracking: #3

## Upstream baseline

```text
oalangomes/AgentsOrchNext
2c85d455c1a8ceed53e128068d0c1da1a5e048cd
```

This slice extracts only backend-neutral retrieval/search contracts.

## Ownership

FetchCore now owns the pre-consumer retrieval language:

```text
QuerySignal
SearchIntent
SearchRequirement
SearchScope
EvidenceObservation
QueryMatchEvidence
SearchRelationship
SearchResult
KnowledgeSearchResponse
KnowledgeSearchBackend
RetrievalConfig
RetrievalBackendCapabilities
```

These values describe acquisition, observations, matches and backend capabilities.
They do not grant authority, requirement satisfaction, lifecycle state or artifact
authority.

## Adapter seam

```text
AgentsOrch QueryPlanning
    -> FetchCore QuerySignal/SearchIntent

FetchCore SearchResult
    -> AgentsOrch EvidenceCandidate
    -> ContextResolver
```

The second conversion remains in AgentsOrch and is intentionally not part of FetchCore.

## Compatibility approach

Shapes are intentionally kept close to the frozen upstream implementation so that
Projection v3 and the existing Solr/OpenSearch/Qdrant adapters can be extracted in
the next slices without mixing a ranking redesign into the repository split.

Any future contract simplification should be a separately tested product decision.
