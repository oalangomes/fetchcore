# Retrieval experiment convention

A promoted experiment records its **hypothesis**, **controlled variable**, **corpus and repository revision**, **Ground Truth**, **configuration and fingerprints**, **metrics**, **baseline**, **result**, **regressions**, and **conclusion**. Change one variable when possible; preserve raw artifacts and exact commands so results can be re-evaluated. Measure quality alongside p50/p95 latency, indexing throughput, context volume, and cost when applicable.

The frozen [baseline v1 protocol](baseline-v1.md) defines its own corpus, arms, metrics, and promotion rules. [Baseline results](results/baseline-v1.md) and the [Solr latency investigation](results/solr-latency-investigation.md) are point-in-time evidence, not current architecture or universal tuning guidance. Generic external regression tooling such as retrievalgate remains outside FetchCore; FetchCore owns deterministic retrieval behavior and metrics.
