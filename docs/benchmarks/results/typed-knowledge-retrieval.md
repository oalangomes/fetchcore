# Typed knowledge retrieval validation

## 1. Acquisition correctness

**PASS for the pinned corpus.** The acquisition gate recovered 17 of 17
positive locators, excluded the wrong-method operation and unsafe environment,
resolved three source-proved relations and did not resolve two unproven joins.
The full method and canonical results are in
[typed knowledge canonical validation](typed-knowledge-canonical.md).

## 2. Canonical knowledge preservation

**PASS for the pinned corpus.** Repository, revision, source checksums and
canonical checksums were checked on 799 typed documents. Projection v3
round-tripped 799 records. An unchanged reindex was a no-op. Deleting one
linked Markdown target changed two documents, removed 20, and matched a full
canonical rebuild. These are local checks, not backend state claims.

## 3. Snapshot consistency

**BLOCKED.** The [offline artifact](../../../benchmarks/typed-knowledge-retrieval/artifacts/offline-preflight.json)
has no backend snapshot. The [read-only service inventory](../../../benchmarks/typed-knowledge-retrieval/artifacts/service-preflight.json)
shows existing resources on the BENCH Solr, OpenSearch and Qdrant services.
The offline artifact records a dirty product tree because the benchmark runner
was under review; its runner and metric source hashes identify the checked
files. The final PR head and CI results must be read from the PR, not inferred
from that pre-commit artifact.
The Docker Compose project and published ports were inspected locally; the
existing resources were not modified. There is no explicit operational write
authorization for a new isolated namespace. The runner requires one before
creating any core, index, collection or embedding.

## 4. Evidence retrieval

**BLOCKED.** No real backend query was issued. SourceRecall@K and exact-ID
EvidenceRecall@K are unavailable. The Ground Truth has 17 separately declared
ranking queries across OpenAPI, Postman and Markdown. No V0.4 result is reused
as a measurement of this protocol.

## 5. Ranking and ContextYield

**BLOCKED.** Evidence MRR, nDCG, Markdown SpanCoverage and line-budget
ContextYield are unavailable. The evaluator reuses the baseline ContextYield
span-coverage definition for Markdown only. API pointers have no exact line
span, so the line-based measures are explicitly unavailable for API cases.
The old closed PR's relevant-character ratio is not called ContextYield.

## 6. Performance and cost

**BLOCKED.** No indexing throughput, incremental backend latency, p50/p95/p99,
time-to-first-candidate, embedding cache hit rate or cost was observed for a
typed backend snapshot. The services share a host with other workloads, so
performance comparability also requires documented isolation. The runner
records unavailable token count and monetary cost as `null`.

## 7. Regressions, limits and decision

No frozen `baseline-v1` runner or V0.1–V0.4 artifact was changed. The
admission ceiling change in the dependency PR is fingerprinted as indexing
contract version 27 and requires an explicit rebuild of older typed snapshots.
The 20 acquisition cases are intentionally varied, not representative of all
repositories. One independently verified ambiguous request is pending and
excluded from promoted metrics. The existing BENCH snapshots are unrelated to
this typed protocol and cannot be used as its baseline. No PASS is assigned to remote
indexing, retrieval, ranking or performance based on a completed offline run.

**Recommendation:** keep typed acquisition opt-in. Do not close the end-to-end
acceptance issue or mark the unresolved relation benchmark criteria complete
until authorized isolated snapshots, deletion attestations and ranked evidence
measurements are recorded and reviewed.
