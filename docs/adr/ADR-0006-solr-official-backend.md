# ADR-0006 — Apache Solr as the official operational retrieval backend

## Status

Accepted (decision dated 2026-11-11; repository adoption through this PR).

## Context

FetchCore turns repositories into canonical, typed, traceable knowledge and exposes evidence candidates through backend-neutral retrieval contracts. Search and vector indexes are rebuildable projections, never the source of truth.

Maintaining operational parity across Solr, OpenSearch and Qdrant has imposed separate provisioning, schema, publication, recovery, CI and benchmark costs. The pre-PR-240 synthetic incremental benchmark at approximately 10,000 documents observed 3 backend writes / 7.92 s for Solr, 10,005 / 5.76 s for OpenSearch and 10,002 / 11.76 s for Qdrant. These observations do **not** prove Solr is fastest or most relevant for every query. The OpenSearch/Qdrant write amplification is partly caused by FetchCore's generation-publication strategy, not a fundamental inability of those engines to update individual records. The Qdrant H1/H1b investigations did not demonstrate a stable end-to-end improvement without changing that strategy.

Solr already supports the repository-oriented operational indexing path and provides the structured/lexical and optional vector capabilities required for the intended product profile, subject to capability negotiation and benchmark validation.

## Decision

1. **Apache Solr is the sole official, actively developed operational retrieval/index backend of FetchCore**. New product examples, operational guidance, primary CI acceptance and performance optimization target Solr.
2. **OpenSearch and Qdrant are excluded from OPERATIONAL entirely**. Their OPERATIONAL workflows are removed, and `scripts/shared_infra.py` rejects these backends when `environment=operational`. They remain optional compatibility/benchmark adapters in DEV/BENCH, without ongoing feature-parity obligations.
3. **Solr is the only OPERATIONAL workflow**, including manual dispatch, automatic post-CI incremental indexing and recovery. Neither manual nor automatic OPERATIONAL workflows exist for OpenSearch or Qdrant. Do not silently fall back to a different backend when OPERATIONAL selects an unsupported adapter.
4. **Canonical Repository Knowledge, Projection, `KnowledgeSearchBackend`, capability negotiation, provenance, versioned fingerprints and consumer-facing contracts remain backend-neutral.** Vendor semantics stay inside adapters/composition. No Solr-specific schema becomes canonical.
5. `local-semantic` remains available for local/development and compatibility scenarios, but is **not** a second official persisted production backend. Existing SDK/serving defaults and opt-in backend IDs must not be silently changed by this ADR; any default change requires a separate, tested migration of runtime behavior and documentation.
6. Preserve OpenSearch/Qdrant library adapters, existing DEV/BENCH compatibility and historical benchmark evidence. Do not delete existing OPERATIONAL collections, containers or volumes as part of the source change. Previously deployed resources may still be running until the operator explicitly stops them; this ADR does not claim host-level shutdown. Any future adapter removal must follow explicit versioned deprecation, release notes and migration checks. No implicit backend fallback.
7. Measure Solr's targeted/structured, lexical, dense/hybrid retrieval, index update cost, p50/p95 latency, Recall@K and failure/rollback guarantees against frozen benchmarks. A primary-backend decision does not waive those quality gates.

## Consequences

- One official operational path gets engineering priority, reducing routine CI/embedding load and maintenance fragmentation.
- Alternate backends remain explicit, capability-dependent DEV/BENCH or external SDK compatibility choices. They are not valid `shared_infra.py` OPERATIONAL selections; no additional operational workflow or resource lifecycle is maintained for them.
- Solr becomes a concentrated operational dependency and needs strong recovery, security and regression tests. In particular, low write counts alone do not establish atomic visibility or rollback equivalence.
- Alternative engines can be reconsidered only if a real workload demonstrates a material quality, latency, cost or operational gap and that evidence exceeds the cost of switching.

## Alternatives considered

- **OpenSearch as the sole backend:** viable lexical/vector engine, but no measured FetchCore retrieval or operational advantage large enough to justify switching the existing reference path.
- **Qdrant as the sole backend:** strong vector retrieval; less aligned with the required lexical/structural baseline and current staged incremental publication is expensive for tiny deltas.
- **A fourth backend (e.g. Vespa):** potential hybrid-ranking capabilities, but adds an unvalidated adapter and operational migration before a demonstrated need.
- **Three equally supported production backends:** retains flexibility but multiplies engineering, test and maintenance cost without validated product value.

## Transition and verification

**NOW:** record this decision; remove OpenSearch/Qdrant OPERATIONAL workflows (including manual dispatch), reject non-Solr OPERATIONAL routing, prioritize Solr in product documentation; retain adapters and existing snapshots without destructive operations.

**NEXT:** verify no other automation can write to retired OPERATIONAL targets; review explicit opt-in runtime defaults in CLI/SDK/MCP, and establish Solr-only primary acceptance and benchmark evidence for retrieval quality, indexing, fail-closed compatibility and rollback. Any host-level shutdown of old containers requires separately verified ownership and operator authorization.

**LATER:** deprecate alternate adapters only with a published migration strategy, verified consumer impact and an appropriate release boundary.

**DO NOT DO YET:** rewrite indexing, add a new vendor, silently discard old resources, or couple the canonical domain to Solr.

## Related issues/PRs

[ADR-0001](ADR-0001-backend-neutral-knowledge.md), [ADR-0002](ADR-0002-capability-adapters.md), [ADR-0003](ADR-0003-operator-runtime-configuration.md), [adapter architecture](../architecture/adapters-and-composition.md), [operational indexing](../guides/self-hosted-indexing.md), [benchmark protocol](../benchmarks/structural-live-2026-11-09.md), [reconciliation record](../operations/backend-reconciliation-2026-11-09.md).
