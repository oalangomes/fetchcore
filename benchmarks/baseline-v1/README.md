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
3. Solr hybrid
4. OpenSearch lexical
5. OpenSearch hybrid
6. Qdrant dense
7. Qdrant hybrid

No unsupported capability or failed backend may be silently replaced.

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

## Current status

The source-level measurements captured on 2026-09-30 were useful experimental
evidence, but they are **not the promoted baseline-v1**. They were produced
before evidence-region Ground Truth became the quality contract.

The final promotion requires a new evidence-aware run of all seven arms after the
Solr performance investigation is understood.

See `docs/benchmark/baseline-v1.md` for metric definitions and the promotion
gate.
