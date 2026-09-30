# FetchCore baseline v1 freeze

This directory contains the immutable input definition for the first FetchCore
retrieval/performance baseline.

## Corpus

- repository: `oalangomes/NeuroTrack_MS`
- revision: `74ce3244956a65baed61f1dae6f498cb2be4f3b7`
- corpus id: `neurotrack-ms-74ce3244`

The corpus is private. FetchCore does not copy source content into this
benchmark directory. Ground Truth stores only retrieval intents and canonical
repository paths.

The selected revision intentionally predates a later harness-only commit so the
baseline corpus is the product repository, not benchmark scaffolding.

## Protocol

- 1 warm-up per query;
- 20 measured repetitions per query;
- Recall@20 / RR / nDCG@20 / useful evidence density;
- p50 / p95 / p99 over repeated measured retrievals;
- max 20 returned results;
- diagnostic candidate window 80 when the backend supports it;
- embedding identity: Ollama + embeddinggemma + 768 dimensions;
- RRF remains the hybrid fusion baseline.

## Frozen arms

1. local lexical
2. Solr lexical
3. Solr hybrid
4. OpenSearch lexical
5. OpenSearch hybrid
6. Qdrant dense
7. Qdrant hybrid

A frozen baseline requires all seven artifacts. Unsupported capability or
environment/schema mismatch fails the run; no arm may silently substitute
another mode.

## Environment

The logical environment profile is
`fetchcore-local-benchmark-v1`.

Each artifact additionally records the observed Python/OS/CPU profile and
backend/embedding service versions. Performance comparisons are valid only
between compatible environment profiles. Quality metrics remain useful across
environments, but latency/throughput must not be compared as if hardware and
services were identical.

## Ground Truth

`ground-truth.json` contains 20 reviewed queries spanning:

- exact identifiers;
- services/controllers;
- tests;
- routes;
- queues/workers;
- configuration;
- Docker/runtime configuration;
- architecture documentation;
- repository development/governance documentation.

Ground Truth identifies evidence by `source_ref`, derived from repository
identity + path. It does not encode semantic satisfaction or organizational
authority.

## Promotion

Artifacts belong under `artifacts/` only after running on the exact corpus
revision and recording environment identity. Unit-test fixtures are not
baseline artifacts.

The final freeze is:

```text
FetchCore revision/tag
+ corpus SHA
+ Ground Truth
+ config fingerprint
+ environment profile
+ seven measured artifacts
```
