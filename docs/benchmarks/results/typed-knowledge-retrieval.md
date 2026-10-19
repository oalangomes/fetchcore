# Typed knowledge retrieval validation

## Acquisition and canonical knowledge

**PASS for the pinned corpus.** The acquisition gate recovered 17/17 positive
locators, excluded the wrong-method operation and unsafe environment, resolved
three source-proved relations and did not resolve two unproven joins. The
[canonical validation](typed-knowledge-canonical.md) records the source checks.
All 799 documents retained repository, revision, provenance and checksums.
Projection v3 round-tripped 799 records. A source deletion removed 20 documents,
updated two, and produced the same canonical set as a full rebuild.

## First authorized backend measurement: projection contract revision 3

The [complete measured artifact](../../../benchmarks/typed-knowledge-retrieval/artifacts/backend-measurement-projection-r3.json.gz)
is the immutable result of the one authorized run on 2026-10-19. Its uncompressed
SHA-256 is `c05bcc863fe1a683ce422ac6f2f4ac559fc4dfecb97042e825e0fd5748c683fc`.
The runner completed with exit code 0 and artifact status `MEASURED`, product
revision `b7038b9e2d036cb1142ce68650aced074978af80`, clean tree, and
projection fingerprint
`sha256:cfd4a63f85c8e4034bf17f0f82f08a47c717d52e75a9180ffb1eb6cad719e039`.
This is a historical measurement of revision 3, not a result for the route fix.

Fresh resources under `fc-typed-732532f2cce2` were created in the authorized
Solr, OpenSearch and Qdrant BENCH services. Each full snapshot attested 799
canonical documents, the same record-set SHA-256
`3caa3f9fca28bdd2d136d4ce871eda3ac462ec1a097b3f07420c3ef1902f317c`,
the pinned revision, indexing/projection fingerprints, `embeddinggemma`, and
768-vector dimensions. Following the source deletion, each backend attested
779 documents and record-set SHA-256
`270f594cd7a961d0473676dd6d3ec253328bde063ca653ce14ff64f9839b29cd`.
The resources were retained. The Qdrant writer-lock collection was temporary;
OpenSearch also created its internal `.tasks` index for asynchronous reindexing.
Neither is counted as a new typed snapshot.

## Real retrieval and ranking

The runner issued five repetitions of each of 17 ranked queries in eight arms,
plus five repetitions of each of 12 route lookups in two targeted arms. The
table reports mean exact-ID EvidenceRecall@K, MRR and nDCG, and observed
latency percentiles in milliseconds. Ranked arms use the configured K; targeted
arms have an effective two-result window. These are measurements, not promotion
thresholds.

| Arm | Queries | Recall@K | MRR | nDCG | p50 / p95 / p99 ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| Solr lexical | 85 | 1.000 | 0.686 | 0.766 | 3.357 / 6.735 / 1156.934 |
| Solr dense | 85 | 0.882 | 0.619 | 0.682 | 12.879 / 32.756 / 1067.437 |
| Solr hybrid | 85 | 1.000 | 0.672 | 0.753 | 11.521 / 18.513 / 969.440 |
| OpenSearch lexical | 85 | 1.000 | 0.686 | 0.766 | 7.026 / 10.820 / 216.888 |
| OpenSearch dense | 85 | 1.000 | 0.683 | 0.757 | 14.882 / 38.348 / 152.186 |
| OpenSearch hybrid | 85 | 1.000 | 0.721 | 0.791 | 18.454 / 29.580 / 36.963 |
| Qdrant dense | 85 | 1.000 | 0.683 | 0.757 | 57.942 / 70.707 / 85.894 |
| Qdrant hybrid | 85 | 1.000 | 0.682 | 0.757 | 96.559 / 132.808 / 157.887 |
| Solr route lookup | 60 | 0.000 | 0.000 | 0.000 | 1.672 / 1.972 / 955.964 |
| OpenSearch route lookup | 60 | 0.000 | 0.000 | 0.000 | 3.616 / 5.054 / 7.792 |

The route lookup returned zero expected IDs because revision 3 omitted typed
OpenAPI/Postman facts from the projected route fields. Revision 4 maps those
validated facts into the existing fields. The revision-4 run below measures
this fix. The full revision-3 artifact contains per-query hit IDs and telemetry.

For the five Markdown queries, the artifact includes SpanCoverage and
ContextYield for 100/250/500/1000-line budgets. Across these budgets,
ContextYield was 1.0 for OpenSearch dense/hybrid and Qdrant dense/hybrid,
0.8 for Solr dense/hybrid, and 0.4 for Solr and OpenSearch lexical. API
pointers have no proved line span, so their line-based measures are `null`.
The embedding cache generated 507 vectors and reused 2,405; its hit rate was
0.826. Token count and monetary cost were unavailable (`null`).

## Second authorized backend measurement: projection contract revision 4

The [complete revision-4 artifact](../../../benchmarks/typed-knowledge-retrieval/artifacts/backend-measurement-projection-r4.json.gz)
is the result of the separately authorized run on 2026-10-19. Its uncompressed
SHA-256 is `89a1aa5c34fbcc7640b01e94fd46bcd75c0b58d4f282e6bd39bc6c8fee444c9d`.
The command ran once (PID 223165, 21:33:39–21:35:38 UTC), exited 0 and wrote
status `MEASURED`. Product revision
`1e6f19f1bf2bb25556608ba2f63e08b12f0b4390` had a clean tree and
projection fingerprint
`sha256:80d19b9400141cd7bb73f7ba7f64de414cd5a896b87c2dc1713959804696cef2`.
Hosted Ollama supplied 768-dimensional `embeddinggemma` vectors. The resource
namespace was `fc-typed-9da2b604ee9b`.

New isolated Solr, OpenSearch and Qdrant snapshots each attested 799 records
with the same full record-set checksum as revision 3. After the one source
deletion, each attested 779 records with the same final checksum as revision 3.
The before/after inventory preserved all pre-existing resource names and
contained exactly the recorded new resources. The temporary Qdrant writer
lock was absent after the run. Both sets of typed snapshots remain retained.

The same 17 ranked queries ran five times in eight arms. The 12 route cases
ran five times each on Solr and OpenSearch. Values below are observed exact-ID
means and latency percentiles; the host was shared, so latency columns are
descriptive only.

| Arm | Queries | Recall@K | MRR | nDCG | p50 / p95 / p99 ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| Solr lexical | 85 | 1.000 | 0.686 | 0.766 | 2.507 / 3.898 / 1055.448 |
| Solr dense | 85 | 1.000 | 0.681 | 0.757 | 9.584 / 26.789 / 1042.166 |
| Solr hybrid | 85 | 1.000 | 0.723 | 0.792 | 11.677 / 18.945 / 1035.205 |
| OpenSearch lexical | 85 | 1.000 | 0.686 | 0.766 | 6.315 / 11.048 / 15.285 |
| OpenSearch dense | 85 | 1.000 | 0.673 | 0.750 | 14.181 / 29.569 / 64.119 |
| OpenSearch hybrid | 85 | 1.000 | 0.723 | 0.792 | 16.083 / 27.597 / 31.680 |
| Qdrant dense | 85 | 1.000 | 0.673 | 0.750 | 54.039 / 63.973 / 69.255 |
| Qdrant hybrid | 85 | 1.000 | 0.688 | 0.762 | 85.952 / 101.908 / 114.913 |
| Solr route lookup | 60 | 0.917 | 0.792 | 0.824 | 1.974 / 2.516 / 1053.238 |
| OpenSearch route lookup | 60 | 0.917 | 0.792 | 0.824 | 3.063 / 3.709 / 9.528 |

For route lookup, the six paths with exactly one canonical candidate recovered
their reviewed IDs in **30/30 observations per backend**. The other six paths
are ambiguous by path alone; five reviewed IDs appeared in the two-result
diagnostic window (25/30 observations per backend). `postman-login` has four
canonical candidates and its reviewed ID was outside that window on both
backends. The raw aggregate 0.917 is therefore not a unique-operation success
rate. Revision 4 fixes the missing route fields, while path-only lookup cannot
identify a single operation in these ambiguous cases.

Markdown SpanCoverage was 1.0 for all eight ranked arms. ContextYield at each
100/250/500/1000-line budget was 1.0 for all dense/hybrid arms and 0.4 for
both lexical arms. API line-based measures remain unavailable. The embedding
cache generated 507 vectors, reused 2,405 and reported a 0.826 hit rate;
token count and monetary cost remain unavailable.

## Reserved workload window on the current PC

The [third authorized measurement](../../../benchmarks/typed-knowledge-retrieval/artifacts/backend-measurement-projection-r4-quiescent.json.gz)
ran once on clean product revision
`7b6dabf0e0787ba6aa51c06dc0254375bea1fec9` from 21:44:33 to
21:46:29 UTC on 2026-10-19 (PID 237625, exit 0, status `MEASURED`). Its
uncompressed SHA-256 is
`29ffb618f4d24abdf69147870fbda92d3fc8a1517ece54b96f8cdc10021235b9`.
The artifact records `performance_comparable=true` for this **temporarily
reserved workload window**. The separate
[isolation attestation](../../../benchmarks/typed-knowledge-retrieval/artifacts/isolation-attestation-r4.json)
records the observed conditions and restoration.

Before the run, the three `shared` containers were running, the three
`operational` containers were already stopped, and the `fetchcore` and
`ai-ops-alan` runners were active. The user authorized pausing the competing
containers, stopping the runners and leaving Windows applications and hosted
Ollama idle. At the start and end of the benchmark, only the three BENCH
containers were running; the `shared` containers were paused, the others
remained stopped, and no runner worker was observed. Sampled Windows CPU was
6% before and 4% after. The three paused containers and two runners were
restored to their initial active states; restoration exited 0. Two earlier
preflight attempts stopped before benchmark execution and also restored state.
This was **temporal workload isolation on a shared physical PC**, not a
dedicated machine. Windows CPU was not sampled continuously, and paused
containers retained memory. The flag is based on the approved configuration
and these observations; the runner does not independently detect isolation.

The same runner, evaluator, Ground Truth, projection and embedding fingerprints
were used in the second and third measurements. Observed p95 latency changed
as follows; the previous run had a shared workload, so this is descriptive
evidence of the reserved window, not a causal performance claim.

| Arm | Shared p95 ms | Reserved-window p95 ms |
| --- | ---: | ---: |
| Solr lexical | 3.898 | 3.195 |
| Solr dense | 26.789 | 23.279 |
| Solr hybrid | 18.945 | 14.412 |
| Solr route lookup | 2.516 | 2.127 |
| OpenSearch lexical | 11.048 | 10.127 |
| OpenSearch dense | 29.569 | 21.868 |
| OpenSearch hybrid | 27.597 | 20.629 |
| OpenSearch route lookup | 3.709 | 4.078 |
| Qdrant dense | 63.973 | 59.791 |
| Qdrant hybrid | 101.908 | 99.174 |

The Solr p99 remained 938–959 ms in the reserved window. The first observation
of each Solr arm accounts for its largest duration; its cause is not established
by this run. All eight ranked arms again had exact-ID Recall@K 1.0, and both
targeted backends recovered the six unique route paths in 30/30 observations.
Solr, OpenSearch and Qdrant each attested 799 records, then the same 779-record
incremental state. The new `fc-typed-e17d832f7dd8` namespace was retained;
pre-existing resource names were preserved and the temporary Qdrant lock was
absent after the run.

## Limits and decision

The first two artifacts record `performance_comparable=false`. The third
records `true` under the documented temporary workload reservation. This
supports comparison among the arms within that window, with the caveats above;
it does not prove a physically dedicated host or remove ordering, cache and
first-query effects. No frozen
`baseline-v1` runner or V0.1–V0.4 artifact was changed. The prior BENCH
snapshots were not used as a baseline or modified.

**End-to-end acceptance remains BLOCKED for performance promotion and broader
ambiguous-operation semantics.** Acquisition, isolated snapshot integrity,
incremental deletion, real ranked retrieval and unique-route lookup have
measured evidence on revision 4. Performance thresholds and regression budgets
have not been approved, and monetary cost remains unavailable. The reserved
window supports measured latency comparison but does not justify a blanket
performance PASS. Revision-3
snapshots are incompatible with revision 4 and remain intact pending an
operator lifecycle decision. A new BENCH run would require another explicit
authorization.
