# Operational indexing extraction

Tracking: #8

## Upstream baseline

```text
oalangomes/AgentsOrchNext
2c85d455c1a8ceed53e128068d0c1da1a5e048cd
```

This slice restores the operational repository-index lifecycle after canonical
Repository Knowledge, retrieval contracts, Projection v3 and backend adapters have
already moved to FetchCore.

## Extracted modules

- `repository/index_contract.py`
- `repository/index_operations.py`
- `repository/incremental.py`
- `repository/incremental_persistence.py`
- `repository/document_incremental.py`
- `repository/orchestration.py`

## Preserved operational behavior

The slice intentionally keeps the proven upstream behavior:

- deterministic indexing fingerprints;
- same-revision clean no-op detection;
- Git-aware delta planning;
- document-aware targeted incremental synchronization;
- snapshot/currentness persistence;
- dry-run with zero persistent writes;
- collection/repository duplicate-writer protection;
- bounded repository orchestration;
- per-repository failure isolation and diagnostics.

## Solr-specific operational contracts

The upstream operational implementation is currently proven primarily through the
Solr store and exposes names such as:

```text
IndexContractSolrSnapshot
GitAwareSolrSynchronizer
DocumentAwareGitSolrSynchronizer
TargetedGitAwareSolrSynchronizer
```

FetchCore preserves those contracts during M0 extraction rather than inventing a
new generic sync abstraction while moving repositories.

This does **not** make canonical Repository Knowledge or retrieval Solr-owned.
Solr remains an adapter. A future backend-neutral synchronization abstraction must
be introduced only when there is a concrete second operational implementation and
equivalence evidence.

## Architecture after this slice

```text
Repository Sources
       ↓
RepositoryIndexer
       ↓
canonical Repository Knowledge
       ↓
RepositoryIndexChanges
       ↓
operational sync
       ↓
Solr store / persisted snapshot

repo A ─┐
repo B ─┼→ bounded orchestration → collection-scoped synchronization
repo C ─┘
```

Search serving remains independently available through Local/Solr/OpenSearch/Qdrant
backends.

## Deliberately deferred

- AgentsOrch compatibility adapter;
- Benchmark 006 equivalence gate;
- MCP serving;
- new multi-repository retrieval benchmark;
- GraphProjection;
- backend-neutral incremental-store redesign;
- GAC/Vivo-specific deployment or authorization.

## Extraction rule

This is still a behavior-preserving extraction. New operational capabilities and
generic synchronization abstractions are follow-up product work.
