# FetchCore namespace migration

Tracking: #17

FetchCore uses its own operational namespace for new installations while keeping
only bounded, explicit compatibility with extraction-era configuration.

## Canonical names

| Concern | FetchCore |
|---|---|
| repository cache | `~/.cache/fetchcore/repositories` |
| repository cache env | `FETCHCORE_REPOSITORY_CACHE` |
| indexing policy | `.fetchcore/indexing.yaml` |
| document indexing policy | `.fetchcore/document-indexing.yaml` |
| repository knowledge | `.fetchcore/repository-knowledge.{json,yaml}` |
| Solr/OpenSearch default | `fetchcore-knowledge` |
| Qdrant default | `fetchcore-vector` |

## Legacy compatibility

The following extraction-era inputs remain compatibility aliases:

- `AGENTSORCH_REPOSITORY_CACHE` when `FETCHCORE_REPOSITORY_CACHE` is absent;
- `.agentsorch/indexing.yaml` when the FetchCore policy is absent;
- `.agentsorch/document-indexing.yaml` when the FetchCore policy is absent;
- `.agentsorch/repository-knowledge.{json,yaml}` when the FetchCore manifest is absent.

Repository-local policy loaders fail closed when both canonical and legacy files
are present. FetchCore does not guess which configuration should win.

## Persisted/versioned identifiers

The serialized reranking query identifier `agentsorch.rerank-query@1` is a
historical protocol identity, not an operational default. It remains unchanged
in this migration so existing fingerprints/replay artifacts are not silently
rewritten. A future FetchCore-native identifier requires an explicit new
serializer identity/version and compatibility tests.

## Migration

For repository-owned configuration:

1. move `.agentsorch/indexing.yaml` to `.fetchcore/indexing.yaml`;
2. move `.agentsorch/document-indexing.yaml` to
   `.fetchcore/document-indexing.yaml`;
3. move repository knowledge manifests from `.agentsorch/` to `.fetchcore/`;
4. replace `AGENTSORCH_REPOSITORY_CACHE` with
   `FETCHCORE_REPOSITORY_CACHE`.

Backend collections/indexes are external persisted stores. Existing explicitly
configured names remain valid; only FetchCore defaults changed.
