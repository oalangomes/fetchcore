# FetchCore baseline v1 freeze

This directory contains the immutable input definition for the first FetchCore
retrieval/performance baseline.

## Corpus

- repository: `oalangomes/NeuroTrack_MS`
- revision: `74ce3244956a65baed61f1dae6f498cb2be4f3b7`
- corpus id: `neurotrack-ms-74ce3244`

The corpus is private. FetchCore does not copy source content into this
benchmark directory. Ground Truth stores retrieval intents plus minimal evidence regions as canonical\nrepository paths and 1-based line ranges. It never copies source content.

The selected revision intentionally predates a later harness-only commit so the
baseline corpus is the product repository, not benchmark scaffolding.

## Protocol

- 1 warm-up per query;
- 20 measured repetitions per query;
- source Recall@20 as a diagnostic;\n- EvidenceRecall@20 and span coverage as primary quality metrics;\n- evidence MRR and evidence nDCG@20 over incremental useful span coverage;\n- useful context line ratio, returned lines/chars and candidate counts;
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

Ground Truth is schema v2. Each query declares one or more logical evidence\nrequirements. A requirement contains sufficient alternatives, and each alternative\ncontains minimal `(source_ref, line_start, line_end)` regions. A source hit alone is\ndiagnostic; evidence is recovered only when a sufficient region set is covered.\n\nThe protocol does not decide consumer satisfaction or organizational authority. It\nonly evaluates whether the retrieval response contained the reviewed technical\nevidence.

## Promotion

Artifacts belong under `artifacts/` only after running on the exact corpus\nrevision and recording environment identity. Retrieval artifacts must use artifact\nschema v2, Ground Truth schema v2 and metrics schema v1, including their SHA-256\nfingerprints and ranked result spans. Source-only pre-freeze runs are retained only\nin Git history and are not promotable baseline artifacts. Unit-test fixtures are not\nbaseline artifacts.

The final freeze is:

```text
FetchCore revision/tag
+ corpus SHA
+ Ground Truth
+ config fingerprint
+ environment profile
+ seven measured artifacts
```
