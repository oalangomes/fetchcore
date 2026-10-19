# FetchCore benchmarks

This directory contains **repeatable measurements of retrieval behavior** on
pinned repository sources with a stated hypothesis, control, Ground Truth and
explicit measurement protocol. Benchmarks yield observations, never consumer
satisfaction or semantic authority.

| Suite | Class | Purpose |
| --- | --- | --- |
| [baseline-v1](baseline-v1/) | Official, frozen | Nine-backend/mode retrieval matrix and ContextYield |
| [search-intent-v2](search-intent-v2/) | Controlled experiment | Paired serving/query representation comparison |
| [targeted-exact](targeted-exact/) | Controlled experiment | Exact acquisition and constrained fallback measurements |

The machine-readable [catalog](catalog.json) is a small inventory, not a new
benchmark runner. Experiment-specific results remain versioned under their
respective protocols. The frozen baseline and artifact checksums are unchanged.

**Not benchmarks:** synthetic extraction/security/provenance checks are under
[tests/acceptance/acquisition](../tests/acceptance/acquisition/). They may
collect timing diagnostics, but these are not promoted as retrieval/performance
benchmarks. Consumer agent/memory studies live under
[studies/repository-learning](../studies/repository-learning/).

Rules for a new benchmark: stable capability name (never an issue ID or work
slice), explicit hypothesis, corpus/revision, controlled variable, Ground
Truth, runner/environment identity, metrics, limitations, raw evidence and
comparability statement. One change at a time; no implicit backend rebuild.
See [experiment conventions](../docs/benchmarks/protocol.md).
