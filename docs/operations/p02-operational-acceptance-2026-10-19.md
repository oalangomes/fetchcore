# P0.2 operational acceptance — 2026-10-19

## Identification and scope

Phase: P0.2. Branch: `codex/p02-operational-preflight-stacked`, stacked on P0.1 PR #210 and P0.0 PR #209. Base SHA: `2acf643ad99d36d8b4d19d352c4607bc646ab4a5`. Review PR: #211. Issues: [Solr #130](https://github.com/oalangomes/fetchcore/issues/130), [OpenSearch #202](https://github.com/oalangomes/fetchcore/issues/202), [Qdrant #203](https://github.com/oalangomes/fetchcore/issues/203), [Readiness #132](https://github.com/oalangomes/fetchcore/issues/132). This is a new controlled execution; closed #181 and frozen BENCH artifacts are not acceptance evidence here.

The user authorized an isolated Compose project `fetchcore-p02-codex`, including Solr, OpenSearch, Qdrant, Ollama, four private volumes, loopback ports 28983/29200/26333/21434, model pull, controlled fixture and exact project cleanup. Services had labels `io.fetchcore.owner=codex-p02-20261019` and `io.fetchcore.purpose=operational-acceptance`. The existing `fetchcore-shared`, `fetchcore-operational` and `fetchcore-lab` projects were not modified. Exact image digests and Compose SHA-256 are recorded in the [sanitized evidence artifact](artifacts/p02-operational-acceptance-2026-10-19.json) and the preflight inventory. The model was `embeddinggemma:latest`, digest `85462619ee721b466c5927d109d4cb765861907d5417b9109caebc4e614679f1`, with a measured 768-dimensional `/api/embed` response. The runtime was an installed wheel outside the checkout, built from implementation commit `9dfa85a63daa5196e0773f294908f0506902788d`. Wheel SHA-256: `a482254e8f75b85ca6688033598da83920ea65e8717a00d5b276c70a546d749e`.

## Capabilities and functional acceptance

| Capability | Solr | OpenSearch | Qdrant |
| --- | --- | --- | --- |
| Lexical | PASS, real query | PASS, real query | NOT_SUPPORTED; explicit `unsupported_capability` |
| Dense | PASS, real 768-dimensional embedding | PASS, real 768-dimensional embedding | PASS, real 768-dimensional embedding |
| Hybrid | PASS, lexical plus dense | PASS, lexical plus dense | PASS, native sparse plus dense; no BM25 parity claim |
| Targeted/exact backend lookup | DECLARED; contract coverage | DECLARED; contract coverage | NOT_SUPPORTED (#86); no vector post-filter substitute |
| Explicit rebuild and incremental | PASS | PASS | PASS |
| Source access | PASS through application CLI, SDK and MCP | PASS through application CLI, SDK and MCP | PASS through application CLI, SDK and MCP; distinct from targeted lookup |
| Snapshot/revision pinning | PASS for controlled fixture and post-restart read | PASS for controlled fixture and post-restart read | PASS for controlled fixture and post-restart read |

Fixture repository: `acme/fetchcore-p02-fixture`. Initial Git revision: `31c26315f41baef983149dcf3582063254360612`; initial corpus checksum `dc0ea5313ed0edcfcd4dc970d2647847fe98446df83450bf86764029cde35664`. Indexing fingerprint `sha256:05b062d902618c1017a6548124dd606bedf2635ec903a4ac0019655a18f9d885`; projection fingerprint `sha256:80d19b9400141cd7bb73f7ba7f64de414cd5a896b87c2dc1713959804696cef2`; vector serializer fingerprint `sha256:5f859289b8b3a594e924545edb1c3ae6178b77ea22cf3ff224d43c5ec7aeb43a`. Serializer was `projection-v3`, version 2. The lexical fixtures advanced to Solr revision `8d0b3422f0dc7a78d70c3786970972ff2e2b1cf0` and OpenSearch revision `30286fc5f14c0b7f73b044d3dc0b979c3ddf79cc`. All three vector fixtures advanced to `4278af4fc62d1fa0e6aac75b1e5f77fd2a5d0e0c`.

In each supported mode, `repo add`, read-only `index plan`, explicit `index rebuild`, CLI `search`, CLI `source`, SDK `search/get_source` and MCP stdio `search/get_source` were exercised. Search hits retained `repository://acme/fetchcore-p02-fixture/src/widgets.py`, revision, checksum and source span where available. Source content matched the recorded hit. After controlled add/update/delete and explicit `index sync`, a second sync was a no-op. All four owned services were restarted without removing volumes. Thereafter, `readiness --json`, search and source passed in the eight supported backend/mode combinations, with the post-sync revision and checksum. The installed wheel was rebuilt from the P0.2 code and used for this post-restart check and the negative probes.

## Operational safety

The vector rebuild indexed two documents for each backend. Controlled vector delta: add 1, update 1, delete 1; each sync embedded only two changed documents in one request. Solr reported 2 writes/1 delete, OpenSearch 6 writes/1 delete, Qdrant 4 writes/1 delete plus one alias promotion. The subsequent vector no-op reported **zero writes, zero deletes, zero embedding requests and zero alias promotions**. Qdrant's generation/alias change is observable and did not change the prior snapshot before promotion. Lexical Solr and OpenSearch reported add/update/delete behavior and `noop: true`; their CLI did not emit backend write/delete counters for every lexical sync, so those specific counters are `NOT_MEASURED` in the artifact.

The first **lexical Solr** rebuild used its 10-second default transport timeout. The backend commit took about 18.7 seconds, so CLI returned typed `index_update_failure` even though a subsequent read-only inspection found a complete, ready snapshot. This is an indeterminate client timeout outcome, not proof that the operation rolled back. An operator-visible explicit rebuild with 60-second timeout succeeded; later incremental sync, no-op, restart and retrieval passed. This is a reliability limitation: configure a timeout appropriate to the Solr host, inspect readiness after timeouts, and decide explicitly whether to rebuild. No automatic repair or silent fallback was added. Vector Solr used 60 seconds and completed its rebuild in 10,996 ms.

Twelve installed-wheel negative probes covered unavailable embedding endpoint, model mismatch, projection mismatch and configured dimension mismatch across the three vector backends. Every `readiness` returned `ready: false`; every search exited nonzero with `unavailable`, `index_stale` or `schema_mismatch`. Qdrant lexical query returned `unsupported_capability`. No negative probe wrote or rebuilt an index. Two defects found by these probes were fixed: OpenSearch search accepted a configured dimension inconsistent with its fixed vector mapping; Qdrant readiness said ready with a configured dimension inconsistent with its active snapshot. Regression tests now cover both. The CLI registered index path now respects an explicit YAML backend while preserving its historical Solr default when no operator backend was supplied.

## Retrieval quality

A fixed two-document fixture and two exact function-name queries formed a small Ground Truth smoke test: `fetchcore_widget_blueprint` → `src/widgets.py`; `newly_added_widget` → `src/new_widget.py`. In each backend's dense and hybrid mode, both targets ranked first among two returned candidates: fixture EvidenceRecall@5 = 2/2 and MRR = 1.0. This only demonstrates that the configured retrieval paths return known evidence on this tiny corpus. It does not establish general relevance, span coverage, context yield or comparative quality. Lexical acceptance used a matching exact function name and source verification. No frozen benchmark baseline was modified.

## Performance and cost observations

**MEASURED_WITHOUT_APPROVED_THRESHOLD.** Single observed vector rebuild adapter durations were Solr 10,996 ms, OpenSearch 1,488 ms and Qdrant 1,712 ms; embedding portions were 870/874/898 ms respectively. Incremental vector sync durations were 9,836/1,539/1,198 ms. The two initial query durations per backend/mode are in the artifact: Solr dense 1,082/1,162 ms and hybrid 1,090/1,137 ms; OpenSearch dense 148/123 ms and hybrid 139/123 ms; Qdrant dense 121/128 ms and hybrid 123/110 ms. These are single observations on one shared host, not a controlled throughput comparison. The post-test `docker stats --no-stream` snapshot showed Ollama 1.067 GiB, Solr 747.6 MiB, OpenSearch 1.094 GiB and Qdrant 170.9 MiB. No cost, p50/p95/p99, time-to-first-candidate, indexing throughput SLO or approved performance threshold was available. Thus performance is neither PASS nor FAIL.

## Checks, reproducibility and artifacts

- `python -m pytest -q tests/contract tests/integration/retrieval tests/unit/adapters`: **583 passed, 8 skipped** (optional integrations); exit 0.
- Ruff on six changed source/test files: exit 0. Mypy on three changed source files: exit 0.
- `python -m build --wheel`: exit 0. Installed wheel check, all eight post-restart profiles and 12 negative cases: exit 0.
- CLI, SDK and MCP serving checks on eight supported profiles: exit 0. All source checks matched the authorized fixture content.
- Raw JSON and Compose inputs remained under `/tmp`; their filenames and SHA-256 values are in the [sanitized evidence artifact](artifacts/p02-operational-acceptance-2026-10-19.json). It contains only whitelisted facts, not operator-local paths, endpoints with credentials or source content. It separates `null` measurements from measured zero.

Reproduction requires explicit owner authorization for a fresh isolated project, a fresh controlled Git fixture, the pinned image/model identities and the same runtime YAML modes. Use `fetchcore capabilities --json`; register the fixture; run `index plan`, `index rebuild`, `search`, `source`, `readiness`; edit/commit only that fixture; run `index plan`, `index sync`, a second `index sync`; restart owned services; repeat `readiness`, `search` and `source`. The local scripts named in the raw evidence manifest are ephemeral execution harnesses and contain operator-specific paths; the committed JSON is the portable result record. The project was cleaned only after evidence was frozen, using the exact owned Compose project and volumes.

## Gates and release implications

- **P0.2 Solr: PASS for the tested lexical, dense and hybrid modes**, with a recorded timeout ambiguity and explicit recovery path. No latency SLO is approved.
- **P0.2 OpenSearch: PASS for the tested lexical, dense and hybrid modes.** The configured-dimension validation defect was fixed and retested.
- **P0.2 Qdrant: PASS for the tested dense and hybrid modes.** Lexical and targeted lookup remain unsupported; source access still works through the application service. The readiness dimension defect was fixed and retested.
- **P0.2 aggregate: PASS for this controlled fixture and supported capabilities.** Performance remains `MEASURED_WITHOUT_APPROVED_THRESHOLD`; broader retrieval quality and production-scale reliability remain unclaimed. PRs #209, #210 and #211 are drafts and have not been merged or released. Recommendation: review and merge in stack order after CI; keep Solr timeout recovery guidance visible in operator documentation before a release.
