# Operational indexing checkpoint — 2026-10-18

**Status:** frozen **source/contract checkpoint**, not a new retrieval-quality or statistically sampled performance baseline.  
**Repository:** `oalangomes/fetchcore`  
**Authoritative revision:** [`ea057d40e8b93760e510fc74eb99bf64bb76cdf7`](https://github.com/oalangomes/fetchcore/commit/ea057d40e8b93760e510fc74eb99bf64bb76cdf7)  
**Convenience ref:** [`baseline/operational-indexing-2026-10-18`](https://github.com/oalangomes/fetchcore/tree/baseline/operational-indexing-2026-10-18)

The commit SHA is the immutable identity. The convenience branch is **not a protected or immutable tag** and is not a product release. This checkpoint captures the code and existing evidence after merged PRs [#191](https://github.com/oalangomes/fetchcore/pull/191) (OpenSearch operational lifecycle), [#192](https://github.com/oalangomes/fetchcore/pull/192) (Qdrant operational lifecycle) and [#197](https://github.com/oalangomes/fetchcore/pull/197) (shared indexing stage telemetry). It does not freeze future `master` changes or backend data.

## What is frozen

- The canonical Repository Knowledge / Projection v3 contract remains backend-neutral. Solr, OpenSearch and Qdrant are adapters to rebuildable projections, not sources of canonical truth.
- Registered `status`, `plan`, `sync`, explicit `rebuild` and capabilities; indexed repository/revision/source identity, schema/fingerprint verification, typed failures, and no implicit rebuild on an incompatible snapshot.
- Qdrant staged immutable collections with alias promotion, repository-scoped interprocess writer lock, pinned physical-generation reads during promotion, retained prior generations, and observable no-op.
- [Shared `IndexStageTelemetry`](../guides/repository-indexing.md): optional `snapshot_load_ms`, `previous_documents_load_ms`, `canonical_delta_index_ms`, `canonical_full_index_ms` and `persistence_call_ms` for the applicable plan/sync/rebuild phases. An absent field means **unmeasured/not executed**, never an invented zero. These stages do not sum to end-to-end CLI latency.
- Retrieval retains scope, revision, provenance and source spans. Search scores/fusion/reranking are observations, not consumer satisfaction or authority: **retrieval != satisfaction != authority**.

This is a *reference checkpoint*, not a promise of API stability or a requirement to preserve implementation details unchanged. Contract evolution must be explicit, versioned when needed, and covered by regression checks.

## Source validation evidence

| Scope | Reference and result | What it proves |
| --- | --- | --- |
| Checkpoint HEAD | `ea057d40e8b93760e510fc74eb99bf64bb76cdf7` | Exact source snapshot after PR #197 |
| CI on that exact HEAD | [GitHub Actions run 39140537349](https://github.com/oalangomes/fetchcore/actions/runs/39140537349) — **success** | Repo CI gates (Ruff/docs, `mypy src`, pytest/coverage, build and runtime-devex) |
| PR #192 final implementation | `0ffc6016ca8375c8cf1137b6c585aea9e588d77c` | Qdrant operational code and evidence incorporated into `master` |
| PR #192 CI / dogfood / Search Lab | [CI](https://github.com/oalangomes/fetchcore/actions/runs/39094998498), [self-dogfood](https://github.com/oalangomes/fetchcore/actions/runs/39094998501), [Search Lab](https://github.com/oalangomes/fetchcore/actions/runs/39094998466) — **success** | Validates the earlier PR #192 HEAD, **not** a new live run of the later checkpoint |
| Real Qdrant operational gate | [2026-10-18 acceptance report](results/qdrant-operational-acceptance-2026-10-18.md) — **PASS** | Real CLI/application lifecycle on pinned sources and prior implementation revisions |

GitHub Actions verification and the versioned operator report are different evidence types. CI does not replace live Qdrant testing, and a historical live acceptance run does not establish that all three live backends were re-tested at this checkpoint SHA.

## Frozen operational observations — Qdrant (historical gate, not remeasured)

The [Qdrant acceptance report](results/qdrant-operational-acceptance-2026-10-18.md) records Qdrant 1.19.0, an existing Ollama `embeddinggemma` provider (768 dimensions), Projection v3, and a clean main source `oalangomes/fetchcore@4b21c08ac0cff66a5a57c657213f110e847b7b11`.

| Measurement | Observed result |
| --- | ---: |
| Main explicit rebuild: documents / points including snapshot | 4,424 / 4,425 |
| Main rebuild: wall / adapter time | 174.72 s / 165,527 ms |
| Main rebuild: document embeddings / requests | 4,424 / 139 |
| Main rebuild: embedding time / logical backend writes | 156,034 ms / 4,425 |
| Same-revision no-op | 0 embeddings, 0 writes/deletes, 0 alias promotions |
| Isolated committed-Git incremental: wall / adapter | 0.70 s / 446 ms |
| Isolated incremental: embedded / reused vectors | 3 / 1 |
| Isolated incremental: logical writes / point deletes | 7 / 2 |
| Single-run dense / hybrid application time | 481 ms / 585 ms |

Main projection fingerprint: `sha256:cfd4a63f85c8e4034bf17f0f82f08a47c717d52e75a9180ffb1eb6cad719e039`. Serializer `projection-v3` v2 fingerprint: `sha256:5f859289b8b3a594e924545edb1c3ae6178b77ea22cf3ff224d43c5ec7aeb43a`. Main indexing fingerprint: `sha256:4a66bd379a149aab3f3ee7f59236f53a5f08ccda7c7ac7c67ddc5ea100824bfe`. Main source corpus checksum: `304dac8c84b402f0cf3495f153a547ec0c341ce2c334c841ca44aa8e3760c4ef`.

These are **single-run point observations** from earlier PR code, not current-checkpoint throughput, p50/p95/p99, or evidence of ranking quality. Logical write counts are not HTTP request counts. The main historical snapshot is revision-pinned and is not proof that this checkpoint HEAD was indexed. The report records a failed unaliased stage retained for operator inspection, not a silently cleaned resource.

## Existing retrieval-quality baseline remains separate

[Baseline v1](baseline-v1.md) is already frozen under its own protocol, corpus, Ground Truth, fingerprints, nine retrieval arms and 20 measured repetitions per query. The [baseline-v1 results](results/baseline-v1.md) contain EvidenceRecall@K, SpanCoverage@K, Evidence MRR/nDCG, context density, and p50/p95/p99. **Do not overwrite those artifacts** with this checkpoint or claim that their results were measured at `ea057d40e8b93760e510fc74eb99bf64bb76cdf7`.

The [V0.2/S0 verification](baseline-v1-s0-verification.md) explicitly classifies its historical retrieval run as `RETRIEVAL_ONLY_OBSERVATION`, **not** an accepted S0 provenance checkpoint. A current-checkpoint retrieval comparison requires new revision-pinned artifacts and snapshot identities, not a provenance upgrade by inference.

## Comparison and promotion policy

For subsequent indexing/retrieval work, preserve this checkpoint as the reference for changed contracts and behavioral regressions. Record the exact base/experiment SHAs, repository/corpus revision, provider, backend capabilities, projection/serializer/indexing/embedding fingerprints, namespace, command and raw measurement artifacts. Fail closed on incompatible identities.

Every meaningful retrieval optimization must define a hypothesis, a single controlled variable where feasible, a stable Ground Truth and comparison protocol, Recall/EvidenceRecall@K and evidence density, p50/p95/p99, observed stage costs, indexing throughput/embedding reuse, and regressions. Use [the existing experiment convention](protocol.md) and an external harness such as retrievalgate when appropriate; do not bake the harness into FetchCore.

Before claiming a **new operational performance baseline at this checkpoint SHA**, run controlled, repeatable, isolated measurement for the intended Solr, OpenSearch and Qdrant workloads. Record repeated wall-time distributions, p50/p95/p99, indexing throughput, point/wire-write distinctions, costs and environment identity. No such three-backend performance experiment is asserted here.

## Reproduce and protect the reference

```bash
git fetch origin
git checkout --detach ea057d40e8b93760e510fc74eb99bf64bb76cdf7
git rev-parse HEAD
python -m pytest -q
python -m ruff check .
python -m mypy src
python -m build
```

These commands validate source behavior locally when dependencies are available; running them is **not** evidence of a real backend lifecycle gate. The existing [repository indexing guide](../guides/repository-indexing.md) defines source binding and explicit operations. Always preflight ownership, endpoints, aliases and revisions before any live write; do not re-run rebuild on shared data just to reproduce the benchmark.

Do not repoint the checkpoint branch, retag past versions, rewrite baseline-v1, modify the release policy, or trigger a release as part of this freeze. A later product or performance checkpoint receives a new identity and separately validated evidence.
