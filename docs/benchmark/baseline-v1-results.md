# FetchCore baseline v1 — final evidence-aware analysis

Issue: #18  
PR: #48  
Execution revision: `e5b364a0f978186c47911246298f7c9fcc6c1631`  
Corpus: `oalangomes/NeuroTrack_MS@74ce3244956a65baed61f1dae6f498cb2be4f3b7`

## Executive summary

The evidence-aware baseline v1 run is internally consistent and suitable for promotion as the
frozen pre-improvement ruler for FetchCore.

- all nine declared backend/mode arms produced schema-v2 retrieval artifacts;
- all arms used the same corpus revision, Ground Truth fingerprint, metric fingerprint,
  manifest fingerprint, environment identity, K=20, one warm-up and 20 measured repetitions;
- all 20 queries were deterministic across all 20 measured repetitions for every arm;
- evidence-aware evaluation materially changes the interpretation of the old source-only run:
  the local lexical backend has SourceRecall@20 = 0.35 but EvidenceRecall@20 = 0.0;
- dense retrieval is almost backend-neutral in quality across Solr, OpenSearch and Qdrant:
  EvidenceRecall@20 = 0.816667 and SpanCoverage@20 = 0.848254 on all three;
- Solr and OpenSearch hybrid do not improve on their dense paths under the frozen RRF baseline:
  EvidenceRecall falls to 0.8 and SpanCoverage falls to 0.822064 / 0.814841;
- Qdrant hybrid reaches the highest evidence coverage in this corpus
  (EvidenceRecall@20 = 0.9, SpanCoverage@20 = 0.931587), but uses substantially more context
  and has much higher query latency than the indexed Solr/OpenSearch paths;
- the persistent Solr transport correction is validated operationally by the promoted run:
  lexical p50 fell from the historical pre-patch ~1893 ms to 17 ms and hybrid from ~2867 ms
  to 52 ms, without changing retrieval semantics;
- the only total miss shared by every dense/hybrid arm is `ai-component-architecture`.
  Indexing telemetry shows all 86 `.puml` files are currently `not_included` by canonical
  RepositoryIndexer acquisition, so this is a measured acquisition-coverage gap rather than
  evidence that vector search failed to rank an indexed document;
- full rebuild indexing remains expensive for Solr: 770.0 s versus 320.7 s OpenSearch and
  318.7 s Qdrant. Query latency and indexing latency must remain separate concerns.

These are baseline findings, not reasons to tune the system before promotion. Fixing them before
the tag would move the ruler while it is being frozen.

## 1. Protocol integrity

| Check | Result |
| --- | --- |
| Retrieval artifacts | PASS — 9/9 |
| Artifact schema | PASS — v2 |
| Protocol | PASS — `fetchcore.baseline-v1` |
| Ground Truth schema | PASS — v2 |
| Ground Truth fingerprint | PASS — `sha256:a03733d1601097afcba5e32dc4b04834471758919300457fb88925fce481c29a` |
| Metrics schema | PASS — v1 |
| Metrics fingerprint | PASS — `sha256:3adba6b1021b0f16624d4759e642fcb1616cd00469aba8abcf7e5d2d0281e894` |
| Manifest fingerprint | PASS — `sha256:c6edcb980d0c96e0ac4dd18073f7cef445fd514fa7131ed39304189e04503af8` |
| Execution revision | PASS — same `e5b364a0f978186c47911246298f7c9fcc6c1631` |
| Corpus revision | PASS — same immutable SHA |
| Query count | PASS — 20 |
| Repetitions | PASS — 20/query |
| Warm-up | PASS — 1/query |
| K | PASS — 20 |
| Observations | PASS — 400/arm |
| Determinism | PASS — all result spans stable across repetitions |
| Environment | PASS — same captured service/runtime identity |

Observed environment:

- Python 3.12.3;
- WSL2 / x86_64 / 8 CPUs;
- Solr 9.10.1;
- OpenSearch 3.8.0;
- Qdrant 1.19.0;
- Ollama 0.34.1;
- `embeddinggemma`, 768 dimensions.

## 2. Aggregate retrieval results

| Arm | Source Recall | Evidence Recall | Span Coverage | Evidence MRR | Evidence nDCG | Useful Context Line Ratio | p50 | p95 | p99 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| local-lexical | 0.350000 | 0.000000 | 0.070563 | 0.118333 | 0.158484 | 0.023448 | 2993 ms | 3775 ms | 3972 ms |
| solr-lexical | 0.500000 | 0.333333 | 0.333333 | 0.187179 | 0.251697 | 0.009964 | 17 ms | 28 ms | 33 ms |
| opensearch-lexical | 0.500000 | 0.316667 | 0.316667 | 0.191071 | 0.239891 | 0.009719 | 28 ms | 40 ms | 48 ms |
| solr-dense | 0.900000 | 0.816667 | 0.848254 | 0.746212 | 0.759794 | 0.046348 | 33 ms | 46 ms | 54 ms |
| opensearch-dense | 0.900000 | 0.816667 | 0.848254 | 0.745833 | 0.759359 | 0.046475 | 42 ms | 61 ms | 68 ms |
| qdrant-dense | 0.900000 | 0.816667 | 0.848254 | 0.745833 | 0.759359 | 0.046606 | 994 ms | 1069 ms | 1130 ms |
| solr-hybrid | 0.900000 | 0.800000 | 0.822064 | 0.629167 | 0.660983 | 0.036357 | 52 ms | 70 ms | 78 ms |
| opensearch-hybrid | 0.900000 | 0.800000 | 0.814841 | 0.583333 | 0.636993 | 0.037125 | 60 ms | 83 ms | 91 ms |
| qdrant-hybrid | 0.950000 | 0.900000 | 0.931587 | 0.770513 | 0.779853 | 0.033132 | 1316 ms | 1458 ms | 1548 ms |

No single metric is semantic authority. The table describes observed retrieval evidence under the
frozen protocol.

## 3. What the evidence-aware Ground Truth changed

The most useful proof from schema v2 is the separation between finding a source and returning the
required evidence span.

The local lexical arm reports:

- SourceRecall@20: 0.35;
- EvidenceRecall@20: 0.0;
- SpanCoverage@20: 0.070563.

This means source-level evaluation would materially overstate the usefulness of the current local
scan output for these scenarios. The backend often finds the correct file, but its small
line-oriented snippets do not fully cover a reviewed evidence requirement.

Examples:

- `medication-update-owner-service`: source recall 1.0, span coverage 0.5, evidence recall 0;
- `medication-create-controller-validation`: source recall 1.0, span coverage 0.3125,
  evidence recall 0;
- `mfa-totp-service`: source recall 1.0, span coverage 0.414141, evidence recall 0.

This validates keeping SourceRecall as a diagnostic rather than a primary quality contract.

Evidence nDCG must also be read together with EvidenceRecall/SpanCoverage: it evaluates ordering of
the evidence that was recovered, not missing evidence. A query may therefore have good nDCG while
still failing to fully satisfy an evidence requirement.

## 4. Dense retrieval is backend-neutral in quality

The strongest backend-neutrality result is the dense comparison.

| Metric | Solr dense | OpenSearch dense | Qdrant dense |
| --- | ---: | ---: | ---: |
| Source Recall | 0.900000 | 0.900000 | 0.900000 |
| Evidence Recall | 0.816667 | 0.816667 | 0.816667 |
| Span Coverage | 0.848254 | 0.848254 | 0.848254 |
| Evidence MRR | 0.746212 | 0.745833 | 0.745833 |
| Evidence nDCG | 0.759794 | 0.759359 | 0.759359 |
| Useful Context Line Ratio | 0.046348 | 0.046475 | 0.046606 |

The query-level recovery pattern is also effectively the same.

Common dense gaps:

- `runtime-config-queue-concurrency`: total miss;
- `ai-component-architecture`: total miss;
- `ai-queue-subject-context`: EvidenceRecall 0.333333 but SpanCoverage 0.965079;
- `docker-local-runtime`: EvidenceRecall 0.333333;
- `architecture-overview`: EvidenceRecall 0.666667.

All other evaluated queries reach full EvidenceRecall under dense retrieval.

This is strong evidence that canonical projection + embedding serialization are controlling
retrieval semantics consistently across vendors.

## 5. Hybrid does not have one universal behavior

### Solr and OpenSearch

Under the frozen RRF configuration, hybrid slightly reduces evidence coverage relative to dense:

| Metric | Solr dense | Solr hybrid | OpenSearch dense | OpenSearch hybrid |
| --- | ---: | ---: | ---: | ---: |
| Evidence Recall | 0.816667 | 0.800000 | 0.816667 | 0.800000 |
| Span Coverage | 0.848254 | 0.822064 | 0.848254 | 0.814841 |
| Evidence MRR | 0.746212 | 0.629167 | 0.745833 | 0.583333 |
| Evidence nDCG | 0.759794 | 0.660983 | 0.759359 | 0.636993 |

The important regressions are not vendor-specific:

- `runtime-config-queue-concurrency` remains a total miss;
- `docker-local-runtime` has source recall 1.0 but evidence recall 0 after fusion;
- `ai-component-architecture` remains a total miss;
- `ai-queue-subject-context` remains partial and loses span coverage after fusion.

This is a useful future RRF/candidate-window benchmark target. It is not evidence that hybrid should
be removed or retuned before the baseline is frozen.

### Qdrant

Qdrant hybrid behaves differently under the current adapter:

- EvidenceRecall rises from 0.816667 to 0.900000;
- SpanCoverage rises from 0.848254 to 0.931587;
- `runtime-config-queue-concurrency` becomes fully recovered;
- `docker-local-runtime` becomes fully recovered;
- only `ai-component-architecture` remains a total miss.

The gain has a material cost:

| Cost | Qdrant dense | Qdrant hybrid | Delta |
| --- | ---: | ---: | ---: |
| p50 latency | 994 ms | 1316 ms | +32.4% |
| returned chars mean | 29,372.95 | 67,598.55 | 2.30x |
| returned lines mean | 875.10 | 1271.05 | +45.2% |
| candidates examined mean | 80.0 | 138.75 | +73.4% |
| Useful Context Line Ratio | 0.046606 | 0.033132 | lower |

Qdrant hybrid therefore demonstrates a real recall/context/latency trade-off. FetchCore should not
turn that trade-off into semantic authority; future routing/budget work should make it explicit and
measurable.

## 6. Persistent Solr transport result

The historical source-level pre-patch run had approximately:

- Solr lexical p50: 1893 ms;
- Solr hybrid p50: 2867 ms.

The evidence-aware run after persistent connection reuse reports:

- Solr lexical p50: 17 ms;
- Solr hybrid p50: 52 ms;
- Solr dense p50: 33 ms.

That is approximately:

- 99.1% lower lexical p50;
- 98.2% lower hybrid p50.

The quality semantics were not changed by the transport patch. The result confirms the latency
investigation: connection lifecycle, not Lucene/BM25/KNN execution, dominated the old Solr
measurement.

Solr is no longer a query-latency outlier in this environment and is now in the same operational
range as OpenSearch for indexed retrieval.

## 7. Query-level structural findings

### `ai-component-architecture`

This is the only total miss common to all dense/hybrid arms.

Ground Truth points to a PlantUML architecture source, while indexing telemetry reports:

```text
.puml discovered: 86
.puml not_included: 86
```

Therefore the canonical indexed paths cannot retrieve that evidence because it is not acquired into
Canonical Repository Knowledge.

This is a FetchCore acquisition-coverage baseline gap:

```text
Repository source exists
→ RepositoryIndexer excludes .puml
→ no canonical chunk
→ no vector/lexical projection
→ retrieval cannot recover it
```

Do not fix this before the baseline tag. Record it and use baseline-v1 to measure the later
acquisition change.

### `runtime-config-queue-concurrency`

- lexical recovers partial evidence;
- all dense arms miss it completely;
- Solr/OpenSearch hybrid also miss it;
- Qdrant hybrid fully recovers it.

This is a strong future controlled case for studying lexical signal preservation and fusion.

### `ai-queue-subject-context`

Dense retrieval reaches only EvidenceRecall 0.333333, but SpanCoverage 0.965079.

That means most reviewed lines are present while at least one logical evidence requirement is not
fully completed. This query is a good guard against optimizing only aggregate line coverage.

## 8. Context efficiency

Dense retrieval currently has the strongest UsefulContextLineRatio among indexed modes:

- Solr dense: 0.046348;
- OpenSearch dense: 0.046475;
- Qdrant dense: 0.046606.

Lexical retrieval returns substantially less useful evidence per returned line:

- Solr lexical: 0.009964;
- OpenSearch lexical: 0.009719.

The local lexical ratio is higher than indexed lexical (0.023448) only because it returns very small
snippets; its EvidenceRecall is still zero. Efficiency must therefore never be interpreted without
coverage.

Qdrant hybrid recovers more evidence than dense but its context ratio falls to 0.033132 because the
returned context budget grows faster than unique reviewed evidence coverage.

## 9. Performance

Indexed query p50:

```text
solr-lexical         17 ms
opensearch-lexical   28 ms

solr-dense           33 ms
opensearch-dense     42 ms
qdrant-dense        994 ms

solr-hybrid          52 ms
opensearch-hybrid    60 ms
qdrant-hybrid      1316 ms
```

The local lexical p50 of 2993 ms is not an indexed-backend comparison. It performs a repository
scan on every query and should remain labeled as a portable/stateless scan path.

Qdrant remains the query-latency outlier. The artifacts establish the observation but do not prove
its cause. A separate one-variable investigation is required before changing transport, query shape
or backend configuration.

## 10. Indexing

The consolidated `artifacts/indexing.json` records a complete canonical scan and full rebuilds for
all three remote stores.

Canonical indexing:

- files discovered: 1189;
- files indexed: 894;
- chunks/documents: 9620;
- canonical duration: 12,359 ms;
- indexing throughput: 778.38 chunks/s;
- traversal complete: true;
- removal detection complete: true;
- truncated files: 0.

Remote full rebuilds:

| Backend | Total | Embedding | Backend overhead approx. | Writes | Deletes |
| --- | ---: | ---: | ---: | ---: | ---: |
| Solr | 770.005 s | 305.202 s | 464.803 s | 9620 | 9620 |
| OpenSearch | 320.715 s | 305.502 s | 15.213 s | 9620 | 0 |
| Qdrant | 318.733 s | 301.808 s | 16.925 s | 9620 | 0 |

The embedding throughput is similar (~31.5–31.9 vectors/s), so embedding does not explain the
extra ~465 s of Solr rebuild time.

Solr full rebuild is approximately 2.4x the OpenSearch rebuild time in this run. This should become
a separate indexing benchmark/investigation; it must not be mixed with retrieval latency.

Qdrant issued 301 embedding requests versus 49 for Solr/OpenSearch for the same 9620 vectors,
reflecting its configured batch size of 32. Despite that, total embedding duration is similar in
this environment.

## 11. Promotion decision

### PASS — promote baseline-v1 after PR review/merge

No measurement-contract defect was found in the final nine-arm evidence-aware artifacts.

The following are known product/retrieval gaps and should remain visible in the frozen baseline:

1. canonical acquisition excludes PlantUML and makes `ai-component-architecture` unreachable by
   indexed retrieval;
2. dense retrieval misses `runtime-config-queue-concurrency`;
3. frozen RRF hybrid on Solr/OpenSearch slightly regresses evidence coverage versus dense;
4. Qdrant hybrid gains coverage at significant latency/context cost;
5. Qdrant query latency is much higher than Solr/OpenSearch in this environment;
6. Solr full rebuild has large backend-side overhead after embedding;
7. local lexical scan finds sources but does not return complete reviewed evidence under the v1
   evidence contract.

Poor numbers are valid baseline data. They are not grounds for silently changing Ground Truth,
candidate budgets, ranking, fusion or source inclusion before promotion.

## 12. Follow-up sequence

### NOW — after the baseline tag

- create a focused acquisition experiment for `.puml` inclusion and measure the delta against
  `ai-component-architecture` plus regressions across all 20 queries;
- preserve the nine artifacts and consolidated indexing artifact as baseline-v1 evidence;
- use retrievalgate externally for future before/after regression gates.

### NEXT

- benchmark targeted/exact fast path (#34) against baseline-v1;
- evolve SearchIntent semantics (#19) with explicit required anchors/alternatives/conjunctions;
- run a controlled RRF/fusion experiment using
  `runtime-config-queue-concurrency`, `docker-local-runtime` and
  `ai-queue-subject-context` as sensitive cases;
- investigate Qdrant query latency independently;
- investigate Solr full-rebuild backend overhead independently.

### LATER

- adaptive candidate budgets and early stop;
- bounded reranking only after an explicit baseline comparison;
- federated multi-repository retrieval;
- bounded graph expansion from strong seeds.

### DO NOT DO YET

- do not tune RRF from this single corpus without a controlled experiment;
- do not make dense or hybrid the universal path;
- do not cache/skip revision or snapshot validation to chase latency;
- do not change Ground Truth to make current retrieval look better;
- do not introduce backend-specific product semantics into the core.

## Final interpretation

The baseline demonstrates two important FetchCore properties simultaneously:

1. backend-neutral contracts are producing nearly identical dense retrieval quality across three
   different stores;
2. backend choice and retrieval mode still have major operational and evidence-budget trade-offs.

That is the intended role of baseline-v1: not to prove that retrieval is already optimal, but to
freeze enough evidence to make every next improvement measurable.
