# Hybrid fusion benchmark (#84)

Decision: **BLOCKED / NOT PROMOTED**. The current dedicated BENCH snapshots are
incompatible with the current Projection v3 contract. No retrieval observations,
parameter comparisons or independent confirmation results were produced. This is
an infrastructure/compatibility block, not evidence that a fusion variant failed.

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
`6cd089db8591f481e6488fed1bd49f2a412fbdda`. A separate immutable checkout of that
revision first attempted the unchanged control and failed Solr snapshot attestation
before retrieval. The new runner then attested all six dense/hybrid arms, also
before any retrieval. Both attempts preserved fail-closed snapshot compatibility.

| Identity | Observed |
| --- | --- |
| Persisted projection version | 3 |
| Persisted projection fingerprint | `sha256:cfd4a63f85c8e4034bf17f0f82f08a47c717d52e75a9180ffb1eb6cad719e039` |
| Runtime projection version | 3 |
| Required projection fingerprint | `sha256:80d19b9400141cd7bb73f7ba7f64de414cd5a896b87c2dc1713959804696cef2` |
| Affected backends | Solr, OpenSearch, Qdrant |
| Requested query observations | 400 per arm, six control arms |
| Executed query observations | 0 |
| Performance comparable | false |
| Promotion | none |

Docker project ownership, volume labels and endpoint bindings were inspected before
starting only the existing dedicated BENCH containers. No container was recreated,
no volume removed, no snapshot rewritten and no rebuild initiated. OPERATIONAL
resources were not changed. The canonical query, models, serializers and projection
fingerprint implementation were not modified by this change.

The [safe control artifact](../../../benchmarks/hybrid-fusion/artifacts/blocked-control.json)
records every typed mismatch, runtime/configuration identities, the raw artifact
checksum and the explicit non-promotion decision. Raw configuration, inspection
records and the complete blocked attempt remain in local private BENCH storage.

## Results and validity

| Experiment | Status | Quality / latency / per-query deltas |
| --- | --- | --- |
| Allocation: nine window pairs, k=60 | Not executed | Unavailable |
| RRF: k=10/30/60/120, legacy windows | Not executed | Unavailable |
| Real finalist confirmation | Not executed | Unavailable |
| Ten frozen additional queries | Source-reviewed, not executed | Unavailable |

No gains or regressions can be inferred from this attempt. Offline unit/adapter
fixtures validate admission, replay and compatibility; they are not benchmark
quality or performance evidence. Unchanged defaults and fail-closed behavior are
the only product conclusions established here.

The next real run requires compatible existing BENCH snapshots or a separately
approved snapshot preparation outside this no-rebuild experiment. No compatibility
check may be bypassed to make historical projections readable by the current
runtime. Retain the original snapshots and historical artifacts. Any newly
prepared snapshot creates a separately identified current control; it does not
retroactively reproduce the historical snapshot.

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
