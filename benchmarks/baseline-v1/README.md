# FetchCore baseline v1 candidate

This directory contains the executable candidate protocol for the first promoted
FetchCore retrieval/performance baseline.

## Corpus

- repository: `oalangomes/NeuroTrack_MS`
- revision: `74ce3244956a65baed61f1dae6f498cb2be4f3b7`
- corpus id: `neurotrack-ms-74ce3244`

The corpus is private. FetchCore stores no source content here.

`ground-truth.json` stores query intent plus reviewed evidence requirements as
repository paths and 1-based line regions. The selected revision intentionally
predates later harness-only changes.

## Candidate protocol

- Ground Truth schema: 2;
- artifact schema: 2;
- metrics schema: 1;
- 20 queries;
- 42 evidence requirements;
- requirements are AND; alternatives inside a requirement are OR;
- SourceRecall@20 is diagnostic;
- EvidenceRecall@20 and SpanCoverage@20 are primary coverage metrics;
- evidence MRR and evidence nDCG measure ranking;
- UsefulContextLineRatio measures useful evidence per returned line budget;
- 1 warm-up + 20 measured repetitions per query;
- max 20 returned results;
- diagnostic candidate window 80;
- Ollama + embeddinggemma + 768 dimensions for dense retrieval;
- RRF is the hybrid fusion baseline.

## Arms

1. local lexical
2. Solr lexical
3. Solr dense
4. Solr hybrid
5. OpenSearch lexical
6. OpenSearch dense
7. OpenSearch hybrid
8. Qdrant dense
9. Qdrant hybrid

No unsupported capability or failed backend may be silently replaced.

## Fingerprint applicability

Promotion requires the environment and benchmark configuration identity for every
arm, plus the retrieval-contract fingerprints that are applicable to the path
actually executed.

This is intentionally capability-aware rather than a blanket requirement that
every arm expose every fingerprint:

- `local-lexical` uses the `local-semantic` safe-scan path directly and does
  **not** consume a rebuildable Projection. A projection fingerprint is therefore
  not expected for this arm;
- Solr, OpenSearch and Qdrant projection-backed arms must record the observed
  Projection fingerprint;
- dense and hybrid paths must record the embedding serializer fingerprint;
- hybrid paths must preserve their fusion input/output fingerprints;
- backend-specific retrieval contracts such as Qdrant sparse-analyzer identity
  must be recorded when applicable.

A missing fingerprint that should exist for the executed capability is a
comparison boundary and must not be silently accepted. The absence of a
non-applicable fingerprint is not an error.

## Artifact safety

Measured retrieval artifacts persist only ranked evaluation coordinates:

```text
rank
id
source_ref
line_start
line_end
```

That is enough to re-evaluate evidence metrics without publishing private source
content.

The runner validates Ground Truth coordinates against the exact local checkout
before measurement.

## Indexing artifact

`artifacts/indexing.json` is the consolidated indexing record for the complete
nine-arm run. The canonical corpus is materialized once and the required remote
projections are prepared once per backend.

It records independent Solr, OpenSearch and Qdrant snapshots and telemetry under
`backends`, including projection/serializer fingerprints, writes/deletes,
embedding work and duration.

Older per-arm indexing files were produced by earlier experimental executions.
They remain recoverable from Git history, but are not required for promotion
because the consolidated artifact preserves the indexing evidence for the final
run without duplicating equivalent rebuilds.

Indexing telemetry is evaluated separately from query latency.

## Current status

The source-level measurements captured on 2026-09-30 remain useful experimental
evidence, but they are **not the promoted baseline-v1**. They were produced
before evidence-region Ground Truth became the quality contract.

A complete evidence-aware run of all nine declared arms was captured on
2026-10-01 with artifact schema v2:

- 20 queries;
- 20 measured repetitions per query;
- 400 measured observations per arm;
- consistent Ground Truth and metrics fingerprints across all arms;
- ranked source spans persisted for offline re-evaluation.

The earlier multi-second Solr retrieval anomaly was traced to per-request
connection setup and removed by the persistent Solr transport. In the current
run, Solr query latency is in the same operational range as the other remote
search backends:

| Arm | p50 | p95 | p99 |
| --- | ---: | ---: | ---: |
| Solr lexical | 17 ms | 28 ms | 33 ms |
| Solr dense | 33 ms | 46 ms | 54 ms |
| Solr hybrid | 52 ms | 70 ms | 78 ms |

Solr full-rebuild indexing remains materially slower than OpenSearch and Qdrant
in this environment. That is retained as separate indexing telemetry and does
not change retrieval quality measurements.

Baseline v1 is therefore in **final review before promotion**, not waiting for a
new evidence-aware execution. Promotion still requires the final artifact review
and tag/freeze decision.

See `docs/benchmark/baseline-v1.md` for metric definitions and the promotion
gate.
