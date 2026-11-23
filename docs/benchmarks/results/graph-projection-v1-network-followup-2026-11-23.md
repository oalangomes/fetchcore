# GraphProjection v1 — controlled network-path follow-up (2026-11-23)

**Scope:** issue #30, comparison of existing `get_related` against opt-in GraphProjection v1 for one exact resolved relationship. These are **observed development-machine measurements**, not production SLAs or evidence of multi-hop performance.

## Frozen inputs and correctness

- Repository: `oalangomes/fetchcore`.
- Corpus revision: `fa94f4ee456710f348d05f16425c5b5a016c867c`.
- Benchmark runner: `scripts/experiments/graph_projection_v1.py`, five repetitions of each path per run, using the same pinned scenario `resolved-call-exact-target`.
- Existing baseline: `get_related`, one hop, status `success`, independent Ground Truth recovered **1/1**.
- GraphProjection: one hop, status `partial`, the same Ground Truth recovered **1/1**. The partial status records incomplete extraction coverage/inconclusive observations; it must not be reported as complete coverage.
- Graph projection contains **227** resolved edges in this corpus.
- The original recorded run remains unchanged: [graph-projection-v1-2026-11-23.json](graph-projection-v1-2026-11-23.json).

## Observed results

| Path / metric | WSL to published Docker port | Python benchmark inside SolrCloud container network namespace |
| --- | ---: | ---: |
| Solr URL | `http://127.0.0.1:52349/solr` | `http://127.0.0.1:8983/solr` |
| `get_related` p50 | 5,721.63 ms | **13.25 ms** |
| `get_related` p95 | 6,161.49 ms | **14.88 ms** |
| GraphProjection one-hop p50 | 19,836.95 ms | **35.20 ms** |
| GraphProjection one-hop p95 | 20,273.11 ms | **37.34 ms** |
| Graph publication | 62.71 s | **3.77 s** |
| `get_related` GT recovered | 1/1 | 1/1 |
| GraphProjection GT recovered | 1/1 | 1/1 |
| GraphProjection status | `partial` | `partial` |

The internal-network graph path costs **21.95 ms** more at p50 than the existing one-hop path (**2.66×** relative, with a small absolute delta). The published-port run costs **14.12 s** more (**3.47×** relative). This comparison does **not** establish zero overhead, equivalence across hardware, or absence of regressions on untested paths.

## Attribution checks

Observed HTTP timing for the same Solr admin endpoint:

| Access path | Observed end-to-end latency |
| --- | --- |
| Request from inside SolrCloud container to `127.0.0.1:8983` | approximately 4 ms |
| Request from inside container to its Docker IP `172.17.0.3:8983` | approximately 6–8 ms |
| WSL to published SolrCloud ports `18983`, `28984`, `52349` | approximately 1.02–1.09 s |
| Windows to published port `52349` | approximately 1.01–1.07 s |
| WSL directly to container IP `172.17.0.3:8983` | connection timed out |

Changing the HTTP `Host` header to the external host while remaining inside the container produced a response in approximately 5 ms. The profiler also showed **6 HTTP requests** for the existing one-hop path and **20 HTTP requests** for GraphProjection, with most wall-clock time blocked on socket reads in the published-port run.

**Interpretation:** the extra ~1-second per-request delay is associated with published-port access in this Docker Desktop / WSL environment, not with Solr query processing alone. The precise responsible forwarding component has **not** been isolated. The 20-versus-6 HTTP call count still indicates additional graph-path overhead, and can be investigated separately without weakening consistency validation.

## Benchmark policy and limitations

- For subsequent comparable **performance** runs, keep the runner and SolrCloud on the same internal Docker network, with fixed corpus, hardware, and sampling procedure; keep external connectivity checks as **separate** integration measurements.
- Preserve historical raw runs. The internal-network numbers above were reported from `/tmp/fc30-internal-results/benchmark.json` by the operator and are transcribed here, **not** a newly generated raw artifact committed to this repository. Commit the full raw JSON separately when available.
- Five samples make p95/p99 descriptive observed order statistics rather than reliable tail estimates. Use more samples and controlled load before accepting any latency SLA.
- This benchmark covers **one** real one-hop Ground Truth; it is **not** a 2–3 hop utility/performance acceptance. Retain separate bounded 3-hop fixture/live checks and their evidence.
- No change to lexical/hybrid retrieval, canonical knowledge, or required graph publication checks is justified from these measurements alone.
- Deployment organization is a separate concern: only `fetchcore-dev`, `fetchcore` (existing operational), and `fetchcore-benchmark` are intended Compose projects, each containing SolrCloud, API, MCP and Search Lab. This measurement does not authorize new standalone Compose projects or an operational migration within issue #30.

## PR review gates still required

1. Confirm final `make check` / CI status against the PR head.
2. Validate bounded 2–3 hop correctness, provenance, scope, stale-generation rejection, and budgets.
3. Verify that no transient benchmark artifacts (for example `.coverage`) are included in the patch.
4. Keep GraphProjection opt-in; do not treat the `partial` result as full extraction coverage.
