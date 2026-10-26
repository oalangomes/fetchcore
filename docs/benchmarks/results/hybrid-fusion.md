# Hybrid fusion benchmark (#84)

Decision: **experiment complete; no promotion**. The three backends completed
control, hybrid capture, offline replay and joint real confirmation, including
the frozen ten-query holdout. The replay selected `rrf-30`; quality confirmation
found no lost Ground Truth evidence. Performance remains non-comparable because
there is no independent exclusive-host attestation, so every promotion gate
remains closed. Offline replay duration is not retrieval latency.

## Frozen protocol and run identity

The run preserved the reviewed corpus and query sets, SearchIntent, Ground Truth,
embedding identity, K=20, one warmup, 20 measured repetitions, 80 acquired
candidates per channel and the existing backend-specific admission defaults.
Each of the 20 canonical queries therefore contributes 400 measured observations
per backend hybrid arm. The additional ten frozen, source-reviewed queries were
used only for independent confirmation after `rrf-30` had been selected.

| Identity | Value |
| --- | --- |
| Execution revision | `4a7af87429310fc6169856583c37085702f5cb20` |
| Runner fingerprint | `4d9332bf28c4b294e935bec1d09401bf5bd17ac6f56fad3b8cdbe330d7f4518d` |
| Runtime fingerprint | `sha256:22d9f11a973960224f274ab51a18ae3bab4177c85df6da6dafb2fca410609104` |
| Corpus revision | `74ce3244956a65baed61f1dae6f498cb2be4f3b7` |
| Effective configuration fingerprint | `sha256:033b8e8c817510ae1a30be30a0511e785b114699004302ea0582dad5b86d96a7` |
| Ground Truth fingerprint | `sha256:a03733d1601097afcba5e32dc4b04834471758919300457fb88925fce481c29a` |
| Model | `embeddinggemma`, 768 dimensions; digest recorded in private artifacts |

The former control and incomplete confirmation are preserved as historical
evidence. They were not reused because their runner fingerprints do not match
this run. No frozen historical artifact, Ground Truth, query set, snapshot,
default or projection was modified.

## Completion and integrity

| Phase | Result |
| --- | --- |
| Control | Complete: 400 observations in each of six dense/hybrid arms (2,400 total); all three per-backend controls and consolidated control validated |
| Capture | Complete: 400 measured acquisitions per hybrid backend; 20 validated query shards per backend; source provenance and real/offline fusion fingerprints validated |
| Replay | Complete: all 13 variants evaluated per backend: nine `{20,40,80} × {20,40,80}` windows at RRF k=60, plus RRF k `{10,30,60,120}`; 20 checksummed replay shards per backend |
| Confirmation | Complete: selected `rrf-30` versus the same control on all three backends; 60 main-query and 30 holdout checkpoints validated; no candidate-acquisition or provenance mismatch |
| Public report | Complete: official report and diagnostics allowlist generated and reviewed; raw captures and private configuration remain outside the repository |

The consolidated artifact SHA-256 values are control
`0b05a891b2483c195ecf98620173d6751e31fa55307789d2c92b39351b3d5ffa`, capture
`6e1fe9d8f831729ec6a76f94ac1d45abbce00e0a67d20b58fd41e9fd05f74c4a`, replay
`351b196e0354164550f18ffd29d86cda2712a3a02a377eec67aba5e36b789f47`, and
confirmation `8d8e422276a7c99c87c2f684fc0937e62a497a0302ad524f88bfe124c8ae243c`.
The public, source-free reports and diagnostics are in
[`artifacts/runs/4d9332bf/`](../../../benchmarks/hybrid-fusion/artifacts/runs/4d9332bf/).
The original historical reports and checksums remain unchanged.

## Control baseline and selected variant

Quality metrics below are means over 20 queries × 20 measured repetitions.
ContextYield columns are ordered @100/@250/@500/@1000 lines. Acquired/admitted
columns show lexical/dense candidates per retrieval; returned is top K=20.
Latency values are real online retrieval timings, but are descriptive only and
must not be compared as performance evidence without an exclusive measurement
window.

| Backend / variant | EvidenceRecall@20 | SpanCoverage@20 | EvidenceMRR | nDCG@20 | UsefulContextLineRatio | Useful-hit density | ContextYield @100/@250/@500/@1000 | Acquired L/D | Admitted L/D | Examined / returned | Confirmation p50/p95/p99 ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | --- |
| Solr control | 0.8000 | 0.8221 | 0.5219 | 0.5742 | 0.04169 | 0.0750 | 0.3887 / 0.6887 / 0.7762 / 0.7887 | 80 / 80 | 20 / 20 | 36.90 / 20 | — |
| Solr `rrf-30` | 0.8000 | 0.8221 | 0.5219 | 0.5742 | 0.04169 | 0.0750 | 0.3887 / 0.6887 / 0.7762 / 0.7887 | 80 / 80 | 20 / 20 | 36.90 / 20 | 66.30 / 130.05 / 153.70 |
| OpenSearch control | 0.8000 | 0.8221 | 0.5505 | 0.5924 | 0.04222 | 0.0750 | 0.3554 / 0.7304 / 0.7929 / 0.8221 | 80 / 80 | 20 / 20 | 36.45 / 20 | — |
| OpenSearch `rrf-30` | 0.8000 | 0.8221 | 0.5505 | 0.5924 | 0.04222 | 0.0750 | 0.3554 / 0.7304 / 0.7929 / 0.8221 | 80 / 80 | 20 / 20 | 36.45 / 20 | 83.32 / 158.59 / 170.79 |
| Qdrant control | 0.9000 | 0.9089 | 0.7380 | 0.7538 | 0.02971 | 0.0775 | 0.4101 / 0.6089 / 0.8422 / 0.8714 | 80 / 80 | 80 / 80 | 144.25 / 20 | — |
| Qdrant `rrf-30` | 0.9000 | 0.9221 | 0.7475 | 0.7600 | 0.03092 | 0.0850 | 0.4101 / 0.6589 / 0.8554 / 0.8846 | 80 / 80 | 80 / 80 | 144.25 / 20 | 1250.94 / 1384.38 / 1440.27 |

On the reviewed set, `rrf-30` is quality-equivalent to control on Solr and
OpenSearch. On Qdrant it leaves EvidenceRecall unchanged and improves
SpanCoverage by 0.01317, MRR by 0.00952, nDCG by 0.00622 and ContextYield at
@250 by 0.05. No backend loses a Ground Truth item. This is a corpus-specific
offline selection, not a production default recommendation.

## Independent holdout and promotion gates

The ten-query holdout was not used for tuning or selection. The selected variant
had zero lost evidence on every backend and passed the quality non-regression
check. Solr and OpenSearch were metric-equivalent to control. On Qdrant, holdout
EvidenceRecall and SpanCoverage remained 1.0; MRR improved by 0.03167 and nDCG by
0.02725. ContextYield@500 increased by 0.10. UsefulContextLineRatio decreased by
0.0000835, a small recorded change that did not trigger the frozen non-regression
gate.

| Backend | Holdout EvidenceRecall@20 | SpanCoverage@20 | MRR | nDCG@20 | ContextYield @100/@250/@500/@1000 | Lost evidence | Quality gate |
| --- | ---: | ---: | ---: | ---: | --- | ---: | --- |
| Solr | 0.9000 | 0.9000 | 0.4119 | 0.5095 | 0.55 / 0.65 / 0.90 / 0.90 | 0 | Passed |
| OpenSearch | 0.9000 | 0.9000 | 0.4143 | 0.5127 | 0.55 / 0.65 / 0.90 / 0.90 | 0 | Passed |
| Qdrant | 1.0000 | 1.0000 | 0.7083 | 0.7823 | 0.50 / 0.90 / 1.00 / 1.00 | 0 | Passed |

All three promotion gates remain **failed/pending** because the BENCH
configuration did not attest an exclusive host. Consequently the measured p50,
p95 and p99 values above are not comparable performance results; no latency or
cost claim and no configuration promotion is made. Confirmation output records
`pending-performance-comparability`. This experiment completes Issue #84's
benchmark execution but does not authorize changing retrieval defaults.

## Operational and resource record

The backends ran sequentially in a detached tmux session using the repository
`.venv` Python 3.11.16. Provisioning remained `reuse_only`; no index rebuild,
container restart, volume operation or OPERATIONAL mutation occurred. The host
did not provide an exclusive measurement window. Confirmation completed in
58:15.25, with process peak RSS 460,260 KiB. The highest process RSS observed
across this execution was 1,738,332 KiB during Qdrant replay; peak capture RSS
was 1,165,356 KiB. Swap remained essentially unused. No execution was interrupted
and no query observation was rerun from a validated checkpoint.

## Historical and implementation context

The original V0.2 evidence, checksums, blocked pre-capture attempt and prior
control report are preserved as historical artifacts. Earlier documentation
that described capture/replay/confirmation as missing referred to that prior
fingerprint and is superseded by this completed run; it is not retroactively
reclassified. The implementation and validation history remains in PR #232.
