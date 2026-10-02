# Repository Knowledge extraction provenance

Tracking: #1

## Upstream baseline

```text
repository: oalangomes/AgentsOrchNext
main SHA: 2c85d455c1a8ceed53e128068d0c1da1a5e048cd
secondary SAP/#300 SHA: 8655d4757f8668b3bc544e896ce0a1df645cc620
```

This slice transplants the canonical Repository Knowledge modules that do not require
AgentsOrch Context or search-backend application contracts.

The transplant intentionally preserves implementation behavior and only rewrites the
internal package prefix:

```text
agentsorch.knowledge.repository
-> fetchcore.repository
```

## Deferred from this slice

These upstream modules are intentionally not copied yet because they still depend on
AgentsOrch search/provider or Solr application boundaries:

- `knowledge_provider.py`
- `index_contract.py`
- `index_operations.py`
- `incremental.py`
- `incremental_persistence.py`
- `document_incremental.py`
- `orchestration.py`

They will be extracted after FetchCore owns the required standalone retrieval/search
contracts. `knowledge_provider.py` itself remains an AgentsOrch compatibility concern.

No new retrieval semantics are introduced by this slice.
