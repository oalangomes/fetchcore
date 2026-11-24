# ADR-0007 — Bounded graph adjacency and paired generation publication

**Status:** Accepted

## Context

Issue #30 requires navigable paths of up to three hops with exact provenance.
The M0 SolrCloud experiment proved `nodes()` and `shortestPath()` work on an
edge-only collection, but did not prove a server-side ceiling on intermediate
fan-out. SolrCloud also has no atomic swap of the knowledge and graph aliases.
Serving a completed graph from a previous knowledge generation would present
stale edges as current.

## Decision

GraphProjection v1 keeps one document per resolved canonical observation in a
separate SolrCloud collection. Navigation uses exact source/target adjacency
queries, one node per request, with `rows = remaining edge budget + 1`, a
deadline, depth at most three, and explicit node/edge/path/expansion/output
budgets. The extra row detects truncation. The application validates every
returned edge against hydrated canonical source and target evidence.

A full graph rebuild stages an immutable physical graph collection, validates
schema, all edges and a versioned manifest, then activates one graph alias.
The manifest names the exact physical knowledge collection and binds repository,
revision, corpus checksum, indexing fingerprint, Projection v3 fingerprint,
GraphProjection fingerprint and graph generation. The graph reader pins both
physical collections and checks both aliases and snapshots before and after
navigation. A knowledge-only sync makes the graph capability unavailable until
a matching graph rebuild. Old physical generations are retained for explicit
inspection and recovery; no automatic reset or deletion occurs.

## Consequences

Exact per-hop queries impose HTTP and Solr query overhead that streaming
traversal might avoid. They provide an enforceable intermediate node/edge
budget and work across shards. The manifest and double checks detect known
publication races but do not turn two aliases into a transaction; graph
unavailability is the safe outcome when a binding cannot be proven. Lexical
and hybrid retrieval remain independent of the optional graph alias.

## Alternatives considered

- `nodes()`/`shortestPath()`: M0 demonstrated paths but not a strict
  intermediate fan-out bound, so they are not exposed as a public traversal.
- `{!graph}`: no demonstrated safe distributed behavior across shards.
- A joint alias switch: SolrCloud offers no atomic two-alias operation.
- In-place graph update: would expose partial rebuilds and obscure generation
  identity. Incremental graph maintenance remains issue #41.

## Related issues/PRs

- Issue #30 and M0 report in `docs/benchmarks/solr-graph-m0-2026-11-11.md`.
- Issue #41 for later incremental graph maintenance.
