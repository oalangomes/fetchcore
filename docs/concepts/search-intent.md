# Search intent and scope

**Current:** `SearchIntent` carries exact terms, semantic terms, hints, and typed `QuerySignal` values. `SearchScope` bounds workspace, repository, revision, paths, knowledge types, mode, and maximum results. `SearchRequirement` is a bounded retrieval allocation hint; its `required` field is not a consumer satisfaction verdict. `RetrievalConfig` selects execution mode and optional capabilities.

The application service currently turns a text query and optional exact terms into an intent. Exact terms do not yet imply a universal targeted lookup. Required anchors, alternatives, branches, expansion identity, and adaptive budgets belong to **future SearchIntent v2 (#19)**, not the current serialized contract. See [retrieval](../architecture/retrieval.md) and [contracts](../reference/contracts.md).
