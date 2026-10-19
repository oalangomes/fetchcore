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
validated facts into the existing fields. Its offline corpus audit confirms
all 12 reviewed routes are present, but **no live retrieval has measured this
fix**. Six routes identify one canonical document; six match multiple documents
by path alone. The runner now records that cardinality per targeted observation.
Raw exact-ID scores remain available, but ambiguous paths cannot establish
unique operation lookup. The full artifact contains the per-query hit IDs and
telemetry for independent review.

For the five Markdown queries, the artifact includes SpanCoverage and
ContextYield for 100/250/500/1000-line budgets. Across these budgets,
ContextYield was 1.0 for OpenSearch dense/hybrid and Qdrant dense/hybrid,
0.8 for Solr dense/hybrid, and 0.4 for Solr and OpenSearch lexical. API
pointers have no proved line span, so their line-based measures are `null`.
The embedding cache generated 507 vectors and reused 2,405; its hit rate was
0.826. Token count and monetary cost were unavailable (`null`).

## Limits and decision

The services shared a host with other workloads, so the artifact records
`performance_comparable=false`. The reported indexing throughput and query
latencies are observations, not controlled performance comparisons. No frozen
`baseline-v1` runner or V0.1–V0.4 artifact was changed. The prior BENCH
snapshots were not used as a baseline or modified.

**End-to-end acceptance remains BLOCKED.** A fresh, separately authorized
run must use a new isolated namespace, revision-4 projection fingerprint
`sha256:80d19b9400141cd7bb73f7ba7f64de414cd5a896b87c2dc1713959804696cef2`,
and a new output path. Review its real route hits, ranked metrics, snapshot
attestations and performance isolation before any PASS claim. Revision-3
snapshots are incompatible with revision 4 and must remain intact until an
operator explicitly decides their lifecycle.
