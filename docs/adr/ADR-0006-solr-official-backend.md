# ADR-0006 — Apache Solr as the official operational retrieval backend

## Status

Accepted (decision dated 2026-11-11; repository adoption through this PR).

## Context

FetchCore turns repositories into canonical, typed, traceable knowledge and exposes evidence candidates through backend-neutral retrieval contracts. Search and vector indexes are rebuildable projections, never the source of truth.

Maintaining operational parity across Solr, OpenSearch and Qdrant has imposed separate provisioning, schema, publication, recovery, CI and benchmark costs. The pre-PR-240 synthetic incremental benchmark at approximately 10,000 documents observed 3 backend writes / 7.92 s for Solr, 10,005 / 5.76 s for OpenSearch and 10,002 / 11.76 s for Qdrant. These observations do **not** prove Solr is fastest or most relevant for every query. The OpenSearch/Qdrant write amplification is partly caused by FetchCore's generation-publication strategy, not a fundamental inability of those engines to update individual records. The Qdrant H1/H1b investigations did not demonstrate a stable end-to-end improvement without changing that strategy.

Solr already supports the repository-oriented operational indexing path and provides the structured/lexical and optional vector capabilities required for the intended product profile, subject to capability negotiation and benchmark validation.

## Decision

1. **Apache Solr is the sole official, actively developed operational retrieval/index backend of FetchCore**. New product examples, operational guidance, primary CI acceptance and performance optimization target Solr.
2. **OpenSearch and Qdrant become compatibility/maintenance adapters**. Do not invest in feature parity, performance experiments or additional operational infrastructure for them without a concrete, measured consumer requirement. Preserve existing contracts and documented manual recovery during the transition.
3. **Automatic post-CI operational indexing is Solr-only**. The OpenSearch and Qdrant operational workflows remain manually dispatchable on `master` for explicit inspection, recovery and compatibility checks, but no longer launch after every successful CI run.
4. **Canonical Repository Knowledge, Projection, `KnowledgeSearchBackend`, capability negotiation, provenance, versioned fingerprints and consumer-facing contracts remain backend-neutral.** Vendor semantics stay inside adapters/composition. No Solr-specific schema becomes canonical.
5. `local-semantic` remains available for local/development and compatibility scenarios, but is **not** a second official persisted production backend. Existing SDK/serving defaults and opt-in backend IDs must not be silently changed by this ADR; any default change requires a separate, tested migration of runtime behavior and documentation.
6. Do not remove OpenSearch/Qdrant adapters, persisted resources, user configuration, or historical benchmark evidence in this PR. Any future removal must follow explicit versioned deprecation, release notes and migration checks. No implicit backend fallback.
7. Measure Solr's targeted/structured, lexical, dense/hybrid retrieval, index update cost, p50/p95 latency, Recall@K and failure/rollback guarantees against frozen benchmarks. A primary-backend decision does not waive those quality gates.

## Consequences

- One official operational path gets engineering priority, reducing routine CI/embedding load and maintenance fragmentation.
- Deployments choosing alternate backends remain explicit, capability-dependent and unsupported for new feature parity; the existing adapter behavior is not silently disabled.
- Solr becomes a concentrated operational dependency and needs strong recovery, security and regression tests. In particular, low write counts alone do not establish atomic visibility or rollback equivalence.
- Alternative engines can be reconsidered only if a real workload demonstrates a material quality, latency, cost or operational gap and that evidence exceeds the cost of switching.

## Alternatives considered

- **OpenSearch as the sole backend:** viable lexical/vector engine, but no measured FetchCore retrieval or operational advantage large enough to justify switching the existing reference path.
- **Qdrant as the sole backend:** strong vector retrieval; less aligned with the required lexical/structural baseline and current staged incremental publication is expensive for tiny deltas.
- **A fourth backend (e.g. Vespa):** potential hybrid-ranking capabilities, but adds an unvalidated adapter and operational migration before a demonstrated need.
- **Three equally supported production backends:** retains flexibility but multiplies engineering, test and maintenance cost without validated product value.

## Transition and verification

**NOW:** record this decision; prioritize Solr in product documentation; stop automatic OpenSearch/Qdrant operational runs while keeping manual recovery; preserve schemas, adapters and existing snapshots. No destructive operations.

**NEXT:** verify runtime defaults and explicit opt-in configurations in CLI/SDK/MCP, evaluate whether a versioned change to any default is warranted; establish Solr-only primary acceptance and benchmark evidence for retrieval quality, indexing, fail-closed compatibility and rollback.

**LATER:** deprecate alternate adapters only with a published migration strategy, verified consumer impact and an appropriate release boundary.

**DO NOT DO YET:** rewrite indexing, add a new vendor, silently discard old resources, or couple the canonical domain to Solr.

## Related issues/PRs

[ADR-0001](ADR-0001-backend-neutral-knowledge.md), [ADR-0002](ADR-0002-capability-adapters.md), [ADR-0003](ADR-0003-operator-runtime-configuration.md), [adapter architecture](../architecture/adapters-and-composition.md), [operational indexing](../guides/self-hosted-indexing.md), [benchmark protocol](../benchmarks/structural-live-2026-11-09.md), [reconciliation record](../operations/backend-reconciliation-2026-11-09.md).
