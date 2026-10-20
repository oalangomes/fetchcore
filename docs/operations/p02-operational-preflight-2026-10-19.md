# P0.2 backend operational preflight — 2026-10-19

## Identification and boundary

Phase: P0.2 read-only preparation. Base SHA: `2acf643ad99d36d8b4d19d352c4607bc646ab4a5` (P0.1 PR #210, stacked on P0.0 PR #209). Branch: `codex/p02-operational-preflight-stacked`. Issues: [Solr #130](https://github.com/oalangomes/fetchcore/issues/130), [OpenSearch #202](https://github.com/oalangomes/fetchcore/issues/202), [Qdrant #203](https://github.com/oalangomes/fetchcore/issues/203). All three remained open on 2026-10-19. #181 was closed with its own scoped functional acceptance; this preflight neither reopens nor reuses its measurements as current proof.

This document records installed-artifact capability declarations and current host inventory only. **No backend was provisioned, indexed, rebuilt, cleaned up or modified.** It is not a functional, safety, quality or performance acceptance report.

## Declared capabilities versus operational proof

The wheel `fetchcore-0.1.0.dev0-py3-none-any.whl` was installed outside the checkout, then `fetchcore capabilities --json` was run for each configured backend. `indexing_capabilities()` declares rebuild/incremental for all three. Source reads are an application service operation over an authorized materialized repository, not a backend targeted-lookup feature. Snapshot/revision checks have contract tests, but no current live run was performed in this phase.

| Capability | Solr | OpenSearch | Qdrant |
| --- | --- | --- | --- |
| Lexical | DECLARED | DECLARED | **NOT_SUPPORTED** |
| Dense | DECLARED | DECLARED | DECLARED |
| Hybrid | DECLARED | DECLARED | DECLARED; native sparse/vector path, not BM25 parity |
| Targeted/exact backend lookup | DECLARED | DECLARED | **NOT_SUPPORTED** (#86) |
| Explicit rebuild / incremental | DECLARED / DECLARED | DECLARED / DECLARED | DECLARED / DECLARED |
| Authorized `get_source` | APPLICATION CONTRACT; live check pending | APPLICATION CONTRACT; live check pending | APPLICATION CONTRACT; does not imply targeted lookup |
| Snapshot and revision pinning | IMPLEMENTED_NOT_VALIDATED here | IMPLEMENTED_NOT_VALIDATED here | IMPLEMENTED_NOT_VALIDATED here |

`DECLARED` is not `PASS`. Qdrant must return an explicit unsupported-semantics result for unsupported lexical/targeted intent; no post-filter vector emulation is acceptable. Exact source reading through `get_source` remains a distinct authorized application operation.

## Read-only environment and ownership inventory

Host: Linux, Docker Desktop 29.8.2, 8 logical CPUs, 19 GiB RAM, approximately 8.7 GiB available memory and 792 GiB free disk at preflight. The following projects were already running from another worktree: `fetchcore-shared`, `fetchcore-operational`, and `fetchcore-lab`. Their resource names and aliases establish that they contain DEV, BENCH and OPERATIONAL data, so none is an authorized test-write target for this execution.

| Backend | Read-only endpoint | Existing resources observed | Write decision |
| --- | --- | --- | --- |
| Solr shared | `127.0.0.1:18983/solr` | `fc-dev-fetchcore-635ab7352c2850f2` and three `fc-bench-*` cores | No writes |
| Solr operational | `127.0.0.1:38983/solr` | `fc-operational-fetchcore-635ab7352c2850f2` | No writes |
| OpenSearch shared | `127.0.0.1:19200` | DEV and BENCH aliases/generations, system indices | No writes |
| OpenSearch operational | `127.0.0.1:39200` | OPERATIONAL and prior PR gate aliases/generations, system indices | No writes |
| Qdrant shared | `127.0.0.1:16333` | DEV and BENCH aliases/generations | No writes |
| Qdrant operational | `127.0.0.1:36333` | OPERATIONAL and prior PR gate aliases/generations | No writes |

Queries used only Solr CoreAdmin `STATUS`, OpenSearch `_cat/indices` and `_cat/aliases`, and Qdrant `/collections` and `/aliases`; each returned exit 0. `curl http://127.0.0.1:11434/api/tags` could not connect (curl exit 7), so the default local Ollama provider is **unavailable**. No model or dimensions were selected or downloaded. Container `docker stats --no-stream` observed shared Solr 774 MiB, OpenSearch 1.229 GiB, Qdrant 80 MiB; operational Solr 691 MiB, OpenSearch 1.046 GiB, Qdrant 148 MiB. These are momentary observations on a nonexclusive host, not performance baselines.

The P0.1 local gate passed on its **separate** isolated Solr project; its lexical evidence does not certify P0.2 vector operation. Read-only recheck found locally cached pinned Solr, OpenSearch 3.8.0 and Qdrant v1.19.0 images. Proposed exclusive loopback ports 28983, 29200, 26333 and 21434 were not listening. No isolated Ollama endpoint/model is available, so vector acceptance also needs an explicitly authorized provider/model installation. The proposed `fetchcore-p02-codex` project, its backend and provider volumes, fixed corpus revision, model/dimensions, fingerprints, rollback and resource-scoped cleanup cannot be certified before an owned target is authorized. The safe rollback for this read-only phase is **none needed**. Future runs must record exact ownership and fingerprints, then clean up only resources they created after verifying alias/snapshot state. BENCH V0.1–V0.4 artifacts and shared resources remain immutable.

## Four acceptance axes

| Axis | Solr | OpenSearch | Qdrant |
| --- | --- | --- | --- |
| Functional modes and consumption surfaces | BLOCKED: no isolated lexical/dense/hybrid run | BLOCKED: no isolated lexical/dense/hybrid run | BLOCKED: no isolated dense/hybrid run; lexical/targeted NOT_SUPPORTED |
| Operational safety, snapshots, failures, incremental/no-op | BLOCKED | BLOCKED | BLOCKED |
| Retrieval quality versus fixed Ground Truth | NOT_MEASURED | NOT_MEASURED | NOT_MEASURED |
| Performance/cost | NOT_MEASURED; no approved SLO | NOT_MEASURED; no approved SLO | NOT_MEASURED; no approved SLO |

No EvidenceRecall@K, SpanCoverage@K, MRR/nDCG, ContextYield, p50/p95/p99, time-to-first-candidate, embedding count, write/delete count, throughput or cost is asserted here. If future measurements exist without a ratified threshold, classify performance as `MEASURED_WITHOUT_APPROVED_THRESHOLD`, not PASS. Historical BENCH reports and #181 remain context, not current P0.2 results.

## Gate and next action

**P0.2 Solr: BLOCKED. P0.2 OpenSearch: BLOCKED. P0.2 Qdrant: BLOCKED.** P0.1's local installed-artifact gate passed, but no isolated owned vector resources or reachable configured provider have been authorized for P0.2. Do not run rebuild, delete, Compose up/down, model pull or backend writes against the observed stacks. If the proposed isolated project is authorized, verify its ownership, record a fixed corpus/model/fingerprints and run capability-specific E2E. Publish separate functional, safety, quality and performance evidence before changing any issue checklist.
