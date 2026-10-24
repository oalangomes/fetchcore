# Hybrid fusion benchmark (#84)

Decision: **PENDING / NOT PROMOTED**. A later control-only run completed against
the current corpus and produced 400 observations for each of six dense/hybrid
arms. The private control artifact is valid, but the subsequent hybrid acquisition
capture is missing from private storage. No replay comparisons, finalist analysis
or independent confirmation can be made without it. This is an incomplete
experiment, not evidence that a fusion variant failed.

## Hypothesis and fixed protocol

Functional canonical lexical acquisition can displace useful dense evidence after
RRF fusion. Independently varying fusion admission windows and RRF k might improve
useful evidence ordering without losing recovered requirements.

The frozen historical reference is SearchIntent v2 evidence V0.2: 20 queries,
42 requirements, K=20, one warmup and 20 repetitions, NeuroTrack_MS at
`74ce3244956a65baed61f1dae6f498cb2be4f3b7`, embeddinggemma/768. Historical files
remain unchanged and are pinned by
[checksums](../../../benchmarks/hybrid-fusion/artifacts/historical-checksums.json).
The [protocol](../../../benchmarks/hybrid-fusion/README.md) contains exact commands,
selection rules, diagnostics and publication constraints.

Historical Solr/OpenSearch hybrid used 20 candidates from each 80-candidate
acquisition; Qdrant fused its eligible sparse/dense acquisition window. The
implementation retains these differences without explicit configuration.

## Identity and observed blocker

The isolated implementation branch was based on
`6cd089db8591f481e6488fed1bd49f2a412fbdda`. An earlier unchanged-control attempt
failed snapshot attestation before retrieval and is preserved in
[the blocked control artifact](../../../benchmarks/hybrid-fusion/artifacts/blocked-control.json).
A later control run on PR head `a0d490afb5655a1748bb5a43b8379c4e45f72a57`
passed current snapshot attestation and completed all six arms. Its sanitized,
source-free report is [control-report.json](../../../benchmarks/hybrid-fusion/artifacts/control-report.json).
The private source-bearing control artifact remains outside the repository.

| Identity | Observed |
| --- | --- |
| Control artifact | `control-fixed.json.gz`, private SHA-256 `6f4f0cff35dd655473e28782cab9b095544ebc54e21c33d152fd10d065ac9cb7` |
| Artifact integrity | gzip and runner schema/fingerprint valid; identity matches current inputs |
| Snapshot attestation | 6 arms passed; 0 snapshot errors |
| Control observations | 400 per arm × 6 arms; 2,400 total |
| Capture artifact | Missing; no complete or partial capture found in the supplied private directory or either worktree |
| Performance comparable | false; no independently attested exclusive measurement window |
| Promotion | none; replay and confirmation pending |

### Completed control baseline

Metrics below are the runner's aggregated control results. Durations are observed
control timings, but are **not comparable performance evidence** because host
isolation was not independently attested. They must not be compared to historical
timings as a promotion result.

| Backend | Arm | EvidenceRecall@20 | SpanCoverage@20 | EvidenceMRR | EvidenceNDCG@20 | ContextYield@100/250/500/1000 | p50 end-to-end (ms) |
| --- | --- | ---: | ---: | ---: | ---: | --- | ---: |
| Solr | Dense | 0.766667 | 0.798254 | 0.709402 | 0.714747 | 0.548254 / 0.749444 / 0.785754 / 0.798254 | 31.108 |
| Solr | Hybrid | 0.800000 | 0.822064 | 0.521935 | 0.574202 | 0.388730 / 0.688730 / 0.776230 / 0.788730 | 50.804 |
| OpenSearch | Dense | 0.841667 | 0.873254 | 0.728846 | 0.751517 | 0.548254 / 0.749444 / 0.860754 / 0.873254 | 35.926 |
| OpenSearch | Hybrid | 0.800000 | 0.822064 | 0.550476 | 0.592443 | 0.355397 / 0.730397 / 0.792897 / 0.822064 | 60.089 |
| Qdrant | Dense | 0.841667 | 0.873254 | 0.728700 | 0.751316 | 0.548254 / 0.749444 / 0.860754 / 0.873254 | 820.707 |
| Qdrant | Hybrid | 0.900000 | 0.908889 | 0.737976 | 0.754087 | 0.410079 / 0.608889 / 0.842222 / 0.896389 | 1185.325 |

There are no candidate-allocation or RRF-parameter deltas, per-query regressions,
candidate contribution diagnostics or eligible finalists yet. Do not infer a
winner from these control-only values.

Docker project ownership, volume labels and endpoint bindings were inspected before
starting only the existing dedicated BENCH containers. No container was recreated,
no volume removed, no snapshot rewritten and no rebuild initiated. OPERATIONAL
resources were not changed. The canonical query, models, serializers and projection
fingerprint implementation were not modified by this change.

The historical [blocked control attestation](../../../benchmarks/hybrid-fusion/artifacts/blocked-control.json)
records the earlier typed mismatches. The current sanitized control report records
safe runtime identities and the private raw artifact checksum. Raw configuration,
inspection records and source-bearing observations remain in local private BENCH
storage.

## Results and validity

| Experiment | Status | Quality / latency / per-query deltas |
| --- | --- | --- |
| Allocation: nine window pairs, k=60 | Not executed; capture missing | Unavailable |
| RRF: k=10/30/60/120, legacy windows | Not executed; capture missing | Unavailable |
| Real finalist confirmation | Not executed; no eligible replay finalist | Unavailable |
| Ten frozen additional queries | Source-reviewed, not executed | Unavailable |

No variant gains or regressions can be inferred without replay. Offline
unit/adapter fixtures validate admission, replay and compatibility; they are not
benchmark quality or performance evidence. Unchanged defaults and fail-closed
behavior remain the only product conclusions established here.

The current snapshots passed the control attestation. Resume only after locating
or otherwise recovering a valid capture compatible with this exact control; if it
cannot be recovered, assess a fresh capture as a new artifact without overwriting
anything. No compatibility check may be bypassed to make historical projections
readable by the current runtime. Retain the original snapshots and historical
artifacts. Any newly prepared snapshot creates a separately identified current
control; it does not retroactively reproduce the historical snapshot.

## Implementation and validation

Changes are limited to optional `HybridRetrievalConfig` fields, shared bounded
admission/capture contracts, thin adapter integration, benchmark orchestration,
source-reviewed confirmation data, compatibility/protocol tests and documentation.
There is no second RRF implementation, backend addition, reranking, adaptive
allocation, query rewriting or change to persisted identities.

Tests cover invalid/direct/mapping configuration, legacy serialization, rejection
before I/O, budget versus result availability, original sparse ranks after
eligibility, acquisition/admission/output separation, source spans/checksums,
deduplication, all three adapters' control/replay equivalence, deterministic
fingerprints, artifact integrity/completeness and quality/cost gate failures.
The PR records executed checks and their final results.
