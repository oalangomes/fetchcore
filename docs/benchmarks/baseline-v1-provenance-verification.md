# Baseline V0.2 after S0: verification record

**Decision: `RETRIEVAL_ONLY_OBSERVATION`.** Audited on 2026-10-15. V0.2 is a
complete, internally compatible retrieval run. The three snapshots currently
present agree with a fresh, read-only canonical computation at the exact V0.2
FetchCore revision, but the V0.2 run did not capture their identity and the
snapshots do not persist the S0 indexing-contract fingerprint. This is strong
evidence about the *currently observed* contents, not proof of which snapshots
were queried throughout the historical run. It does not meet the
`ACCEPTED_S0_CHECKPOINT` provenance gate. There is no evidence of an incompatible
retrieval protocol that would require `INVALID_FOR_COMPARISON`.

## Revisions, protocol, and inventory

| Item | V0.1 | V0.2 |
| --- | --- | --- |
| FetchCore revision | `e5b364a0f978186c47911246298f7c9fcc6c1631` | `3cd62ba28a6da2a716711e867d0162f15f9c9076` |
| Corpus | `oalangomes/NeuroTrack_MS@74ce3244956a65baed61f1dae6f498cb2be4f3b7` | same |
| Run status | `complete`, migrated pre-versioning manifest | `complete`, no reused artifacts |
| Index preparation | yes; `indexing.json` present | no; `indexing_artifact=null` |
| Retrieval | nine schema-v2 artifacts, 20 queries × 20 measured repetitions per arm | same |
| Derived evaluation | schema-v1 `context-yield.json`, budgets 100/250/500/1000 lines | same |

**VERIFIED:** The offline comparator validates all nine arm files, 3,600
observations per run, 20 distinct iterations for each query and arm, schema and
protocol versions, corpus and Ground Truth identity, configuration hashes,
aggregate metrics and p50/p95/p99 from observations. It independently recomputes
the entire ContextYield artifact from frozen ranked spans. All 20 ranking lists
are stable across the 20 repetitions within each arm and run; latency varies.
The V0.2 local `.run-state.json` agrees with `prepare_index=false` and the
recorded corpus and FetchCore revisions. It is machine-local and excluded from
version control. The V0.2 runner validates a clean corpus checkout before a run;
its saved state does not attest whether the FetchCore checkout itself was clean.

**VERIFIED:** Ground Truth v2 fingerprint
`sha256:a03733d1601097afcba5e32dc4b04834471758919300457fb88925fce481c29a`;
retrieval metrics v1 fingerprint
`sha256:3adba6b1021b0f16624d4759e642fcb1616cd00469aba8abcf7e5d2d0281e894`;
ContextYield metric fingerprint
`sha256:f021672682b6cee1ae420636b567924321ab65ab894580be738ab0241d17b693`;
line-budget fingerprint
`sha256:31303d7a762466fdeeeae2fa148d5e10f8175cad6614735d84e9fcbafd0daeac`;
manifest fingerprint
`sha256:c6edcb980d0c96e0ac4dd18073f7cef445fd514fa7131ed39304189e04503af8`.
Projection fingerprint remains
`sha256:cfd4a63f85c8e4034bf17f0f82f08a47c717d52e75a9180ffb1eb6cad719e039`;
dense/hybrid embedding serializer remains
`sha256:5f859289b8b3a594e924545edb1c3ae6178b77ea22cf3ff224d43c5ec7aeb43a`.
All relevant per-query fusion input/output fingerprints remain in the frozen arm
artifacts and are exposed by the comparator output.

**VERIFIED:** S0 merge `dfad9701d6d276d6b4a814a454d27248b0e6abd0`
(#163) is an ancestor of the measured V0.2 revision. S1/S2/S3/S4 merges are
later than `3cd62ba`; the current `master` is not the measured code. The V0.1
run preceded S0. The original V0.1 indexing artifact reports 894 admitted
files, 9,620 documents and checksum
`f5f8bdf0512db7b4c932ce404ffa07f296e5036aa8b8fd7e10fad509cf5496e4`.
V0.2 reused indexes and has no indexing telemetry. The interrupted earlier
V0.2 on `fix/baseline-v1-resume-v0-2` was excluded from this audit.
The 13 V0.2 public measurement/report files were checked byte-for-byte against
`bench/baseline-v1-s0-clean@c4b49f79fe3b4835a7794b6cb9a3d417a773371a`
(13/13 match); the local `.run-state.json` was excluded. The aggregate SHA-256
over sorted `filename + NUL + file bytes + NUL` is
`123a78db168d4e0809ab05d2ae04d76ef0a9d73e6c45c4197a87987e3ef9df74`.
The PR does not duplicate those large artifacts or publish private source data.

## Snapshot inspection, read only

The inspection used the active repository-scoped physical index/collection behind
each backend's configured namespace, not an assumption that the bare namespace
was itself a physical index. Only metadata, coordinates and checksums were
retrieved for the audit; no source payload or vector dump is published.

| Backend | Observed snapshot | Current knowledge records | Identity and content checks | S0 contract |
| --- | --- | ---: | --- | --- |
| Solr `fetchcore-knowledge` | ready; indexed 2026-10-13 18:36 UTC | 9,009 | expected repository/revision, checksum, Projection v3, 768-dimensional vectors on all records | **UNVERIFIED:** indexing fingerprint absent |
| OpenSearch repository index under `fetchcore-knowledge` | ready; indexed 2026-10-13 18:48 UTC | 9,009 | same repository/revision/checksum, Projection v3 and embedding snapshot identity | **UNVERIFIED:** indexing fingerprint absent |
| Qdrant repository collection under `fetchcore-vector` | ready; indexed 2026-10-13 18:53 UTC | 9,009 | same repository/revision/checksum, Projection v3, embedding and sparse analyzer snapshot identity | **UNVERIFIED:** indexing fingerprint absent |

**VERIFIED, current state:** All 27,027 inspected knowledge records have the
expected repository and document revision. Each backend has 9,009 distinct
coordinate/checksum tuples across 887 source paths; pairwise set differences are
zero. The snapshots report the same corpus checksum
`3ddfb3ce2745067a6fcbeb96fb8da2dcd72ad49afc13d770528fdbe455caab63`.
Their embedding identity is `ollama/embeddinggemma`, 768 dimensions, serializer
mode `projection-v3` version 2 and the fingerprint above. Qdrant additionally
reports sparse analyzer fingerprint
`c13fcfc1b38ca0f920ebecd74edbd7530464ec52df0db1f13327c737e86e18af`.

**VERIFIED, independent canonical computation:** Running
`prepare_repository_index_runtime(...).indexer.index(workspace)` at the exact
`3cd62ba` source revision, without backend writes, produced 9,009 documents,
887 admitted files, the same corpus checksum, and S0 indexing-contract
fingerprint
`sha256:dda7fefa29c88372d3146f9614f1f3714e77cbfb86b6cb74fb1f02fb5149d9b5`.
It scanned 1,189 tracked files and reported seven API-artifact exclusions:
one OpenAPI, one Swagger, three Postman Collections and two Postman environments.
Those seven recognized files exist in the frozen corpus; none of their paths
appears in any current backend's 887 indexed paths. The 611-document difference
from V0.1 and seven-path difference are consistent with the S0 admission change,
but count differences alone are not proof of causality or corruption.

**UNVERIFIED, historical state:** The backend snapshots expose projection and
embedding fingerprints, but no persisted indexing-contract fingerprint. V0.2
did not record a snapshot checksum, immutable generation ID or backend indexing
telemetry at query time. The current snapshot timestamps precede V0.2, yet a
later modification retaining the same metadata cannot be excluded from the
frozen run alone. No missing fingerprint is inferred from a matching document
count or recreated. To certify a future checkpoint, use isolated or explicitly
authorized indexes, perform a controlled S0-compatible build and record the
persisted indexing fingerprint, generation identity, corpus checksum and
per-backend snapshot identity with the retrieval run. Do not rebuild these
potentially shared indexes as part of this audit.

## Offline retrieval comparison

All values below are means over 400 measured observations per arm. Each cell is
`V0.1 → V0.2`. `Rank` is the number of 20 query rankings whose exact order
changed; `Member` counts changed top-20 coordinate sets. The comparator emits
per-query means and ranges for every metric, plus the observed fingerprints.

| Arm | SourceRecall@20 | EvidenceRecall@20 | SpanCoverage@20 | MRR | nDCG@20 | Useful line ratio | Rank | Member |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| local-lexical | .350→.350 | .000→.000 | .071→.071 | .118→.118 | .158→.158 | .023→.023 | 1 | 1 |
| solr-lexical | .500→.500 | .333→.333 | .333→.333 | .187→.188 | .252→.252 | .010→.011 | 15 | 10 |
| solr-dense | .900→.900 | .817→.817 | .848→.848 | .746→.746 | .760→.760 | .046→.048 | 14 | 11 |
| solr-hybrid | .900→.900 | .800→.800 | .822→.822 | .629→.629 | .661→.662 | .036→.037 | 13 | 9 |
| opensearch-lexical | .500→.450 | .317→.317 | .317→.317 | .191→.191 | .240→.240 | .010→.010 | 15 | 11 |
| opensearch-dense | .900→.900 | .817→.817 | .848→.848 | .746→.746 | .759→.760 | .046→.048 | 13 | 10 |
| opensearch-hybrid | .900→.900 | .800→.800 | .815→.815 | .583→.583 | .637→.638 | .037→.038 | 11 | 8 |
| qdrant-dense | .900→.900 | .817→.817 | .848→.848 | .746→.746 | .759→.759 | .047→.047 | 11 | 7 |
| qdrant-hybrid | .950→.950 | .900→.900 | .932→.932 | .771→.787 | .780→.798 | .033→.034 | 20 | 15 |

**OBSERVED:** EvidenceRecall@20 and SpanCoverage@20 are unchanged on all nine
arms; no reviewed requirement is newly recovered or lost in the modal rankings.
SourceRecall drops from .50 to .45 on OpenSearch lexical: it found fewer
GT-bearing sources but still returned enough spans for the same fully covered
requirements. Changes in MRR/nDCG reflect positions of useful spans, not new
evidence. All query rankings are internally stable over 20 repetitions, so the
between-run differences are not within-run ordering noise.

| Arm | ContextYield@100 | @250 | @500 | @1000 | Examined | Returned | Lines | Chars | p50/p95/p99 ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| local-lexical | .071→.071 | .071→.071 | .071→.071 | .071→.071 | 13.1→13.0 | 7.4→7.3 | 24.6→24.3 | 1,032→1,027 | 2993/3775/3972→2455/3414/4526 |
| solr-lexical | .217→.217 | .258→.258 | .333→.333 | .333→.333 | 76.6→75.5 | 19.6→19.4 | 758.9→656.5 | 30,264→27,143 | 17/28/33→39/59/74 |
| solr-dense | .632→.632 | .807→.807 | .836→.836 | .848→.848 | 80→80 | 20→20 | 888.5→797.3 | 29,525→27,180 | 33/46/54→51/86/98 |
| solr-hybrid | .481→.518 | .750→.750 | .787→.787 | .815→.815 | 37.4→37.2 | 20→20 | 775.1→737.6 | 28,579→28,469 | 52/70/78→61/95/112 |
| opensearch-lexical | .225→.225 | .242→.242 | .292→.317 | .317→.317 | 76.6→75.5 | 19.6→19.4 | 754.8→670.4 | 31,405→29,001 | 28/40/48→46/90/117 |
| opensearch-dense | .632→.632 | .807→.807 | .836→.836 | .848→.848 | 80→80 | 20→20 | 890.9→801.5 | 29,531→27,296 | 42/61/68→64/104/136 |
| opensearch-hybrid | .431→.468 | .750→.750 | .793→.793 | .815→.815 | 37.3→37.3 | 20→20 | 743.0→703.7 | 27,938→27,817 | 60/83/91→94/154/199 |
| qdrant-dense | .632→.632 | .807→.807 | .836→.836 | .848→.848 | 80→80 | 20→20 | 875.1→811.4 | 29,373→27,897 | 994/1069/1130→1046/1631/1977 |
| qdrant-hybrid | .442→.492 | .751→.751 | .857→.857 | .919→.894 | 138.8→138.5 | 20→20 | 1,271→1,173.2 | 67,599→64,412 | 1316/1458/1548→1367/1771/2083 |

**OBSERVED, cost:** Returned lines and characters generally fall, while
candidate counts are nearly unchanged. Query latency p50/p95/p99 generally
rises in V0.2. These are observations, not causal performance gains or
regressions: Python changed 3.12.3→3.11.16 and Ollama 0.34.1→0.35.1; the
corpus admission/index generation, FetchCore revision and runtime load were not
controlled together. Other captured service versions stayed the same.

## Qdrant hybrid diagnosis

**VERIFIED from frozen artifacts:** All 20 exact top-20 rankings differ;
15 have changed membership and five change only order. Each query has exactly
one ranking across its 20 repetitions in each run. Both versions use 80 dense
and 80 sparse candidates per query, then bounded RRF fusion; the union examined
averages 138.75→138.5 (range 122–153→124–153). Projection, serializer and
sparse analyzer fingerprints agree; all 20 per-query fusion-input fingerprints
and all 20 fusion-output fingerprints differ between runs. These facts show
changes before or at fusion and exclude a mere presentation-order change. They
do not identify which upstream variable caused the changed channel candidates
or ranks. The fusion kernel's deterministic tie ordering uses score, best
channel rank and candidate ID; no within-run tie instability is observed. The
reported V0.2→live 20/20 ranking match is a point-in-time reproduction, not a
snapshot provenance attestation.

**OBSERVED, useful positions and budgets:** `mfa-http-routes` moves its first
useful span from rank 2 to rank 1; its MRR increases by .5, ContextYield@100
from 0 to 1, and its whole-span pack changes from 0 to 60 lines. Both runs
recover the requirement at 1,000 lines. `docker-local-runtime` moves its first
useful span from rank 2 to 3, reducing MRR by .166667 with unchanged full
coverage. `runtime-config-queue-concurrency` has the same total returned line
count but its 1,000-line prefix loses half the reviewed span coverage (1→.5);
the full top-20 evidence remains recovered. Thus Qdrant's mean
ContextYield@100 improves .05 while @1000 falls .025; neither alone summarizes
the ranking change. No RRF tuning or backend-specific core logic is warranted
by this audit.

## Acquisition, projection, retrieval and evaluation

**VERIFIED acquisition:** S0 blocks raw Postman/OpenAPI/Swagger artifacts in
safe scanning and canonical indexing. The S0 security regression tests use
synthetic sentinel secrets and assert they do not reach generic scan, canonical
documents, projection or retrieval. The measured corpus scan independently
reports the seven exclusions above. The V0.1 acquisition telemetry predates
S0 and describes 9,620 documents; a live S0 canonical computation describes
9,009. These are different admission states.

**VERIFIED projection, current state:** The current three rebuildable backend
projections agree on content coordinates and checksum. Their snapshot
indexing-contract provenance remains unverified. Retrieval artifacts describe
which candidates were returned, not how every backend record was admitted.

**VERIFIED evaluation scope:** The 42 frozen evidence requirements test the
existing 20 tasks. Stable EvidenceRecall for them does not establish that
Postman/OpenAPI knowledge was retained. The S0 quarantine tests establish
secret exclusion on synthetic artifacts; they are a separate acquisition and
safety gate, not a substitute for retrieval quality. FetchCore returns evidence;
consumers decide satisfaction and authority.

After typed extraction S1/S2/S3/S4, repeat the independent acquisition checks:
recognized artifact counts and skip reasons; safe admitted typed facts and
source spans; no raw credential values; deterministic operation/request/section
identity; canonical checksum and chunk diff; per-backend projection equivalence,
deletes and fingerprint compatibility; then the separate typed extraction
Ground Truth gates for API operations, Postman requests and Markdown sections.
Run a new retrieval benchmark only on controlled, recorded snapshots with the
relevant typed evidence Ground Truth. Keep the frozen 42 requirements and v1
metrics unchanged; compare any new protocol against them explicitly.

## Reproduction and decision boundary

```bash
PYTHONPATH=src python benchmarks/baseline-v1/compare_runs.py \
  --baseline benchmarks/baseline-v1/artifacts/V0.1 \
  --candidate benchmarks/baseline-v1/artifacts/V0.2 \
  --output /tmp/fetchcore-s0-comparison.json
```

The comparator reads frozen JSON and local Ground Truth only; it does not
contact Solr, OpenSearch, Qdrant or Ollama and writes only the requested output
file. It refuses missing files, mismatched schemas, Ground Truth, metric,
budget, configuration or stable retrieval fingerprints, and recomputes derived
ContextYield. Full per-query deltas and repetition ranges remain available in
its JSON output without committing private or bulky run artifacts. Both V0.1 and V0.2 measurement directories are now versioned in the main
tree, so this offline comparison works directly from a clean checkout. The V0.2
run remains classified as `RETRIEVAL_ONLY_OBSERVATION`; restoring its files does
not certify historical snapshot provenance.

**Formal verdict: `RETRIEVAL_ONLY_OBSERVATION`.** A new controlled index build
or an independently verifiable historical snapshot attestation would be needed
to promote S0 provenance. No rebuild was performed during this verification.
