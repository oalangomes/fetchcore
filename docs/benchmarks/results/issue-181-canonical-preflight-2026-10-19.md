# Issue #181 — integrated canonical preflight (2026-10-19)

**Decision: BLOCKED for end-to-end acceptance.** The safe, backend-free canonical and Projection v3 gates below pass. No S1–S5 snapshot was written to Solr, OpenSearch or Qdrant, so real ranked retrieval and backend comparison remain unmeasured.

## Scope and identities

- FetchCore product revision tested: `fb1712574d0869e87a3ffd6f4532abadeb747650` (`master`, after merged PR #194). The acceptance harness and this report are the only planned PR changes; an unrelated untracked Solr regression note was left untouched.
- Protocol: `fetchcore.issue-181-integrated-canonical-v1`, in [`benchmarks/typed-extraction-s1-s5/integrated.py`](../../../benchmarks/typed-extraction-s1-s5/integrated.py).
- Ground Truth: [`integrated-ground-truth.json`](../../../benchmarks/typed-extraction-s1-s5/integrated-ground-truth.json), SHA-256 `49ecd3919babfef3ac845f1b9a28a1bc9b45bc5168152ec35e7c85fe65890915`. It combines existing redistributable S0, S4 and S5 fixture facts. This is reviewed synthetic fixture truth, not real-repository ranking truth.
- Corpus: 15 copied fixture files under `api/`, `markdown/`, `references/`; synthetic repository `fetchcore/issue-181-safe-fixtures`; revision is the SHA-256 digest of sorted path→content SHA-256. Each source hash is in the [raw canonical artifact](../../../benchmarks/typed-extraction-s1-s5/artifacts/integrated-canonical-v1.json).
- Projection v3 fingerprint: `sha256:cfd4a63f85c8e4034bf17f0f82f08a47c717d52e75a9180ffb1eb6cad719e039`.

## Reproduction and observations

```bash
python -m pip install -e ".[dev]"
python benchmarks/typed-extraction-s1-s5/run.py --output /tmp/fetchcore-181-synthetic-gate.json
python benchmarks/typed-extraction-s1-s5/integrated.py --output /tmp/fetchcore-181-integrated.json
```

Both commands passed locally. The existing gate passed S0, S2, S3, S4 and S5 contracts. The integrated gate used the same 15 source files for each arm and varied only the selected indexer or S5 resolver:

| Arm | Canonical documents / paths | Indexing fingerprint | Observation |
| --- | ---: | --- | --- |
| S0 default | 30 / 10 | `sha256:b707fb986db0936b704dcb2958696942353fd66efbf78f0dfcd9a42c6c454a82` | Five API artifacts quarantined, including Postman environment |
| S2 OpenAPI opt-in | 39 / 13 | `sha256:40ff58a7e279fc06fad051e4869e2c707932c4ebeb0a4c81b583a557a1d6a494` | OpenAPI/Swagger admitted; Postman collection and environment quarantined |
| S3 combined, S4 Markdown present | 41 / 14 | `sha256:b316c10960aa4689b4f44305d4fd6aed872466009781c52f3ac7cf0f5c34e9d9` | Postman requests admitted; environment quarantined |
| S5 local resolver opt-in | 41 / 14 | `sha256:9e02384633a564b57083484b7eecdb94a960325d7b1e2bd8eef3122cee398572` | Three expected resolved links, no forbidden/wrong-method resolution |

The final arm recovered exactly **8/8** operation/request facts and **6/6** exact Markdown spans. The synthetic secret sentinel appeared in neither canonical serialization nor Projection v3 records. Every canonical document retained the explicit repository, corpus revision, source checksum, document checksum and source reference. All **41/41** projection records hydrated back to identical canonical evidence. A repeated run produced zero changes. Deleting `references/architecture.md` produced **1 removal, 2 updates, 38 unchanged** documents; the incremental canonical result equaled a full rebuild, and the deleted target lost its resolved relation. The harness also rejects the known wrong-method route and an unrelated receiver. Per-arm canonical digests and all source checksums are recorded in the raw artifact.

These are deterministic correctness observations over a small fixture corpus. They do not establish indexing throughput, real retrieval quality, p50/p95/p99, embedding cost, or effectiveness on a real repository.

## Backend ownership preflight — read only

The local Compose project `fetchcore-bench-dedicated` was running with Solr at `127.0.0.1:28983`, OpenSearch at `127.0.0.1:29200` and Qdrant at `127.0.0.1:26333`. Docker labels show all three containers belong to that project and use its persistent data volumes. Solr is standalone, so collection-list returned HTTP 400; its core-status API showed the existing NeuroTrack_MS core with 9,003 records including the control record. OpenSearch reported an existing NeuroTrack_MS index with 9,003 records. Qdrant reported one existing NeuroTrack_MS collection. The generated BENCH configuration for `oalangomes/fetchcore` points to a different default namespace, but this preflight did not establish exclusive ownership of that namespace or permission to create it.

The baseline-v1 V0.4 artifacts from merged PR #194 remain frozen and were not modified. Historical V0.2 still has the query-time snapshot-attestation gap documented in PR #179; the operator's acceptance to proceed does not convert it to an attested historical measurement. The S0–S5 gate in PR #180 is synthetic, not live retrieval.

## Remaining acceptance work

1. Select a safe, controlled **real repository** corpus and independently review operation, request, section and relation Ground Truth. Pin a committed source revision and exact indexer configuration. The current 15-file corpus is only a synthetic CI smoke.
2. Allocate fresh, issue-owned Solr, OpenSearch and Qdrant resources with explicit operational authorization for create/write/rebuild/delete. Do not target existing NeuroTrack_MS or shared DEV/OPERATIONAL resources.
3. Project the opt-in canonical corpus to each backend. Attest record sets, repository/revision, schema, indexer/projection/serializer/embedding fingerprints, generation/snapshot identity, incremental updates and deletes before query evaluation.
4. Run actual typed-evidence queries against attested snapshots. Measure EvidenceRecall@K, SpanCoverage@K, evidence MRR/nDCG, ContextYield, useful evidence density, candidate/context volume, p50/p95/p99, indexing time and embedding cost. Compare controlled arms one variable at a time and record regressions.

**Recommendation:** keep #181 and the S5 acceptance issue #160 open. The current evidence supports the synthetic canonical/projection gate only; it cannot justify PASS for real end-to-end acceptance or changing the default indexer.
