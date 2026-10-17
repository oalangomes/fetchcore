# Solr ANN breadth gate (#188, 2026-10-17)

Decision: **PASS for the tested Solr configuration** `candidate_window=80,
ann_search_breadth=160, max_results=20`. The adapter default remains the
compatible `topK=candidate_window`; deployments that need the measured recall
must set the Solr option explicitly. This is an observed mitigation, not an
exact-neighbor guarantee or a result generalized to other corpora.

## Hypothesis and controlled variable

The V0.4 miss occurred during Solr approximate KNN acquisition: exact cosine
ranked both missing chunks first, while Solr `topK=80,limit=80` omitted them.
Requesting `topK=160` while retaining `limit=80` should recover their evidence
without increasing FetchCore's candidate budget. The only experimental setting
changed was Solr `ann_search_breadth` (80 → 160). Projection v3, serializer,
model, query text, embeddings, scope, filters, Ground Truth, K=20, lexical
policy, and RRF were held fixed.

## Identity and method

- FetchCore implementation revision: `988558875615f820625d1db8dc46330d3672c7eb`.
  Both paired runs used this clean revision. V0.4 baseline used
  `01259c2ef77ff134d989445b5a93b28bdbe568f4`.
- Corpus: `oalangomes/NeuroTrack_MS@74ce3244956a65baed61f1dae6f498cb2be4f3b7`.
  BENCH project: `fetchcore-bench-dedicated`; Solr 9.10.1 at
  `127.0.0.1:28983/solr`; resource
  `fc-bench-neurotrack_ms-5f6a70bb2b4b3145`. Readiness and Docker project
  ownership were checked. No index write, rebuild, migration, or cleanup ran.
- The live snapshot before and after retrieval matched V0.4:
  `snapshot:5f6a70bb2b4b3145038a0ce2a412e736e997055907885de5efc813a790ee8098`;
  9,002 knowledge documents; corpus checksum
  `c651ddd6b21ba679c5c54c003b3cafaf1be66aa9d6b6a245a517535c9e98af2c`;
  projection fingerprint
  `sha256:cfd4a63f85c8e4034bf17f0f82f08a47c717d52e75a9180ffb1eb6cad719e039`;
  serializer fingerprint
  `sha256:5f859289b8b3a594e924545edb1c3ae6178b77ea22cf3ff224d43a`.
  Ollama `embeddinggemma`, 768 dimensions, was reachable at the recorded BENCH
  endpoint.
- Existing `run_bench.py` and frozen `fetchcore.baseline-v1` ran only Solr
  dense and hybrid: 20 original queries × 20 measured repetitions per arm,
  plus warmup. The two runs were paired by query and iteration (400 pairs per
  arm). All 800 pairs had identical query-vector SHA-256 digests. The control
  reproduced V0.4 ranked spans for every observation and its quality
  aggregates. The existing ContextYield evaluator used the same Ground Truth.
- Complete raw observations, ordered top-20 result identities, timing stages,
  derived ContextYield and the full 80-candidate acquisition order remain
  outside the public repository under
  `~/.local/state/fetchcore/solr-ann-188/final/{control,candidate}/V0.1`.
  The run config is `~/.local/state/fetchcore/solr-ann-188/run-config.json`
  with `provisioning=reuse_only`, `performance_comparable=false` and
  `cleanup=retain`. Reproduce with `run_bench.py --run-config <config>
  --workspace <clean private checkout> --output <new directory>
  --arm solr-dense --arm solr-hybrid`; add
  `--solr-ann-search-breadth 160` for the candidate. Run
  `context_yield.py --artifacts <run/V0.1>` for each result. The read-only
  `capture_solr_ann_candidates.py` script at revision
  `801af9a12f49ed9e0bf116669c870fdca6ba7765` captured one additional
  80-candidate acquisition per query, verified each vector digest and top-20
  order against all 20 paired benchmark repetitions, and wrote
  `ann-candidates.json` in each private run directory. Invoke it with the
  same config and workspace, `--artifact <run/V0.1/solr-dense.json>`,
  `--output <run/V0.1/ann-candidates.json>`, and the breadth option only for
  the candidate. Do not use `--prepare-index`.

An initial exploratory candidate was discarded: the wrapper copied a settings
mapping and failed to write the option back, so its recorded effective settings
and telemetry both showed `topK=80`. The wrapper was corrected and tested;
that interrupted artifact is retained locally as `candidate-invalid-wrapper`
and is excluded from every result below.

## Quality (means across 400 observations per arm)

| Metric | Dense 80/80 | Dense 160/80 | Hybrid 80/80 | Hybrid 160/80 |
| --- | ---: | ---: | ---: | ---: |
| EvidenceRecall@20 | 0.716667 | 0.816667 | 0.700000 | 0.800000 |
| SpanCoverage@20 | 0.748254 | 0.848254 | 0.722063 | 0.822064 |
| EvidenceMRR | 0.645513 | 0.745513 | 0.562500 | 0.637500 |
| nDCG@20 | 0.658979 | 0.758979 | 0.581932 | 0.663478 |
| ContextYield@100L | 0.631587 | 0.631587 | 0.492857 | 0.492857 |
| ContextYield@250L | 0.706587 | 0.806587 | 0.625000 | 0.725000 |
| ContextYield@500L | 0.735754 | 0.835754 | 0.687500 | 0.787500 |
| ContextYield@1000L | 0.748254 | 0.848254 | 0.705952 | 0.805952 |
| UsefulContextLineRatio | 0.046334 | 0.047434 | 0.034944 | 0.036997 |

The two affected queries recovered their expected spans in all 20 repetitions
of dense and final hybrid results. `health-resource-collection` stayed at full
EvidenceRecall. No other query lost EvidenceRecall, SpanCoverage, MRR, nDCG or
ContextYield at any measured line budget. The table below shows every query.
All omitted per-query quality deltas are zero. `Rank changes` counts
repetitions whose ordered top-20 candidate IDs changed, dense/hybrid.
`Acquisition overlap/same rank` counts shared IDs and IDs at the same rank
in the 80-candidate read-only sidecars.

| Query | ΔRecall D/H | ΔMRR D/H | ΔnDCG D/H | ΔCY@250/@1000 D/H | Rank changes D/H | Acquisition overlap/same rank |
| --- | --- | --- | --- | --- | ---: | ---: |
| agent-development-rules | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 | 71/33 |
| ai-component-architecture | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 | 80/80 |
| ai-controller-queue | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 | 78/53 |
| ai-queue-subject-context | 0/0 | 0/0 | 0/0 | 0/0 | 20/20 | 78/15 |
| ai-worker-processing | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 | 77/70 |
| architecture-overview | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 | 77/41 |
| docker-local-runtime | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 | 67/23 |
| health-controller | +1/+1 | +1/+0.5 | +1/+0.630930 | +1/+1 | 20/20 | 41/0 |
| health-controller-tests | +1/+1 | +1/+1 | +1/+1 | +1/+1 | 20/20 | 13/0 |
| health-resource-collection | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 | 75/57 |
| home-active-subject-data | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 | 76/42 |
| home-prompt-controller | 0/0 | 0/0 | 0/0 | 0/0 | 20/0 | 79/16 |
| medication-controller-tests | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 | 79/32 |
| medication-create-controller-validation | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 | 78/29 |
| medication-update-owner-service | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 | 79/59 |
| mfa-controller-errors | 0/0 | 0/0 | 0/0 | 0/0 | 20/20 | 74/8 |
| mfa-http-routes | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 | 78/62 |
| mfa-route-tests | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 | 79/50 |
| mfa-totp-service | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 | 80/80 |
| runtime-config-queue-concurrency | 0/0 | 0/0 | 0/0 | 0/0 | 20/0 | 72/11 |

UsefulContextLineRatio decreased only in three dense queries:
`ai-queue-subject-context` (−0.004762),
`home-prompt-controller` (−0.002213) and
`mfa-controller-errors` (−0.000074). Hybrid
`mfa-controller-errors` increased by 0.000390. These changed returned
contexts without losing reviewed evidence or ContextYield. The ordered top-20
identities and spans are in the retained raw baseline artifacts; the separate
read-only sidecars preserve all 80 acquisition IDs and their ranks per query.

## Operational observations and limits

| Metric | Dense 80/80 → 160/80 | Hybrid 80/80 → 160/80 |
| --- | ---: | ---: |
| Acquired dense candidates | 80 → 80 | 80 → 80 |
| Candidates considered by existing response/fusion | 80 → 80 | 37.15 → 37.10 mean |
| Returned candidates | 20 → 20 | 20 → 20 |
| Returned characters mean | 24,989.65 → 27,542.35 | 26,838.30 → 28,137.30 |
| Returned lines mean | 732.15 → 807.85 | 707.10 → 739.45 |
| Total p50 / p95 / p99 ms | 28 / 37 / 44 → 30 / 38 / 42 | 45 / 55 / 68 → 49 / 61 / 72 |
| Dense acquisition mean ms | 11.463 → 12.383 | 12.082 → 13.463 |
| Fusion mean ms | n/a | 0.168 → 0.190 |

`candidates_examined` and `dense_candidates_acquired` count FetchCore
observations, not Lucene/HNSW graph visits. The external 80-candidate and final
20-result budgets were respected. The larger returned content comes from
different selected chunks, not more results. The host was not exclusively
reserved (`performance_comparable=false`), so all latency deltas are
observational and cannot establish a causal performance regression or guarantee
future cost. No OpenSearch or Qdrant behavior was changed or retested.

## Artifact checksums and decision

| File | Control SHA-256 | Candidate SHA-256 |
| --- | --- | --- |
| `bench-run.json` | `7c7a096ae4d9950dde566a84f794a9cc5553d224506260ae1b7dcd3f972faad2` | `09afbb70193e3f36719fa9378313ef85e177b3ebfb4bdb23d438fe1118235c44` |
| `solr-dense.json` | `ed3b50de2e3900b55da625519074d4f5df654b87cfc7a9277d2ba048c16e1204` | `5ceb86494776d50243cc2bf6f3f66c497f8dc8dc65d94be001fba2415d052eed` |
| `solr-hybrid.json` | `84793fc12d7bef3cbb0f4a7930b29366f2caeefa22b0789410cee465fb0e6c55` | `dd7179a21e59f3752d20548b8dcaeae5726c5ca10886e1ef2b2bfe543c502a0e` |
| `context-yield.json` | `05e636df8ac18a8cb9b35586e5b0e2c8026e00faccb599ad57fb9a49d53692b1` | `f410afacbb4dd3c0e68132a4f8d3eb63d714525ad4882361f2c95521ccd5d08d` |
| `ann-candidates.json` | `59d2dd5cfe300f90da5bb10475e7ea6b5af2e1294e4db19538ac33bd899be861` | `2eb3f66909d4e2a4ac401605f7cd6be3036b5d9d38200d48f88c28fbe11e616d` |

**PASS:** the observed Solr quality regression is removed for this snapshot
with `ann_search_breadth=160` and an 80-candidate window. Compatibility favors
an opt-in Solr setting: changing the adapter default for all existing consumers
would change retrieval quality and cost without a multi-corpus result or
exclusive-host latency comparison. The release configuration for the affected
workload should set `ann_search_breadth: 160` explicitly. The unchanged
default remains the reproducible control. Issue #188 should remain open until
the PR checks and final gate decision are attached. This experiment does not
approve the separate S1–S5 acquisition gate or a release.
