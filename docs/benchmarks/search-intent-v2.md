# SearchIntent v2 serving translation experiment

**Status: measured issue #19 implementation.** The hypothesis is that preserving canonical text and removing lossy serving translation reduces zero-result failures without core-level query analysis. This experiment evaluates that boundary change; it is not a general retrieval or ranking improvement claim.

## Controlled variable and corpus

The changed variable is the serving representation: `SearchIntent(semantic_terms=(query,))` versus `SearchIntent(canonical_query=query)` and its native lexical translation. The same semantic field boosts, BM25 settings, document projection, candidate limit and corpus were used. There is no query expansion, anchor inference, weight adjustment, RRF change or reranker change in the dogfood measurement.

- Baseline code: FetchCore `32fb976` (current master at implementation start).
- Corpus: clean detached checkout of that same revision, repository `oalangomes/fetchcore`.
- Projection: 2,202 documents, indexed once using baseline code; no reindex between arms.
- Solr: 9.10.1, disposable `issue19` core on port 18983 with the repository configset.
- Ground Truth: the six existing [self-dogfood scenarios](../../dogfood/scenarios/fetchcore-self.yaml), unchanged.
- Retrieval: lexical, top 10; one warmup and 20 measured repetitions per scenario and arm.
- Native smoke: real MCP stdio discovery, search and `get_source`, using the existing [dogfood runner](../../scripts/dogfood_mcp_eval.py).
- Timing environment: local workstation, with the two frozen local-lexical baseline jobs also running. Timing is descriptive, not an isolated performance promotion.

The [comparison runner](../../benchmarks/search-intent-v2/run.py) invokes `RetrievalApplicationService` under the same binding used by MCP. It records source-tree and scenario hashes, corpus revision, ranked source paths, per-observation latency, returned candidates and existing telemetry. MCP smoke is separate from those repeated application-level observations.

## Metrics and results

Expected-path hit means at least one reviewed path appears in the top 10. SourceRecall@10 is the fraction of listed expected paths recovered, averaged over scenarios. MRR uses the first expected-path rank. These are source-level metrics; they are not frozen baseline-v1 evidence-span Recall@K and do not prove that the returned content is sufficient for a consumer.

The paired run of the final implementation produced the following measurements. No Ground Truth was changed to improve the result.

| Metric | Before | After |
| --- | ---: | ---: |
| Expected-path hit@10 | 1/6 (16.67%) | 5/6 (83.33%) |
| SourceRecall@10 | 0.0833 | 0.8333 |
| MRR | 0.1667 | 0.3889 |
| Zero-result rate | 83.33% | 0% |
| Mean candidates examined | 0.1667 | 10 |
| Application latency p50 | 1018.595 ms | 1022.137 ms |
| Application latency p95 | 1035.537 ms | 1039.453 ms |
| Application latency p99 | 1069.847 ms | 1048.027 ms |

The existing MCP smoke independently reproduced those expected-path and MRR results: [before](../../benchmarks/search-intent-v2/artifacts/mcp-before.json), [after](../../benchmarks/search-intent-v2/artifacts/mcp-after.json). The full repeated [before observations](../../benchmarks/search-intent-v2/artifacts/serving-before.json) and [after observations](../../benchmarks/search-intent-v2/artifacts/serving-after.json) preserve every measured rank, path and latency. Source-tree hashes identify the exact measured implementations; the final after artifact matches the committed runtime source. The small p50/p95 increases are retained; latency differences are not promoted as an improvement because the environment was not isolated.

The authority-boundary scenario regressed from rank 1 to rank 2 even though aggregate MRR improved. The incremental-sync scenario still misses the reviewed top-10 paths. Its expected paths point to compatibility modules; implementation modules moved during earlier decomposition. The actual `src/fetchcore/adapters/retrieval/solr/incremental_persistence.py` implementation appears at rank 9, while the reviewed `backends/` compatibility paths remain absent. That Ground Truth should be reviewed separately, not changed inside this experiment to raise the score.

Local lexical retrieval still uses its existing phrase/term matcher; no general improvement in natural-language local recall is claimed. Dense/hybrid requests with declared groups or anchors now explicitly reject rather than attempting a lossy representation. Qualified-symbol matching depends on values actually present in the projection; this is not a symbol resolver.

## Compatibility gate

All 20 frozen baseline-v1 intents produce byte-equivalent canonical mappings, Solr requests, OpenSearch requests, local query representations, sparse terms and embedding text. The aggregate JSON SHA-256 captured independently from baseline code is:

```text
4a3b18e742c447e878b1bf35ef4b78370c98e1f1db5b110269ae04c4882ccc12
```

The [compatibility test](../../tests/test_search_intent_v2.py) checks that digest against the new implementation. Document serializer/projection fingerprints and embedding identities were not changed. The existing frozen baseline runner completed 400 measured observations per version (20 queries × 20 repetitions, plus warmups) on NeuroTrack revision `74ce3244956a65baed61f1dae6f498cb2be4f3b7`. All ranked result spans, source/evidence metrics, result sizes and candidate counts matched exactly across every observation; configuration, Ground Truth and metric fingerprints also matched. See the [comparison summary](../../benchmarks/search-intent-v2/artifacts/frozen-local-comparison.json) and compressed raw artifacts ([before](../../benchmarks/search-intent-v2/artifacts/frozen-local-before.json.gz), [after](../../benchmarks/search-intent-v2/artifacts/frozen-local-after.json.gz)).

| Frozen local-v1 metric | Before | After |
| --- | ---: | ---: |
| SourceRecall@20 | 0.35 | 0.35 |
| EvidenceRecall@20 | 0 | 0 |
| Evidence MRR | 0.118333 | 0.118333 |
| SpanCoverage@20 | 0.070563 | 0.070563 |
| Zero-result rate | 60% | 60% |
| Mean candidates examined | 13.1 | 13.1 |
| Latency p50 | 3347 ms | 3333 ms |
| Latency p95 | 4273 ms | 4316 ms |
| Latency p99 | 4595 ms | 4616 ms |

The zero evidence recall and 60% zero-result rate remain existing limitations of this local v1 arm, not improvements delivered by v2. The observed p95/p99 increases are recorded rather than hidden. The two local arms ran concurrently; timing is descriptive. Remote dense/hybrid quality is not re-promoted by these measurements; their v1 input representations are covered by the equivalence gate, and unsupported v2 groups fail before execution.

## Reproduction

Create a clean detached worktree at `32fb976`. Start an isolated Solr using `infra/solr/configset`, then index that worktree once with the baseline source tree on `PYTHONPATH` and the operational `fetchcore index sync` command. Both comparison arms must reuse the same core and snapshot revision.

```bash
PYTHONPATH=/path/to/before/src python benchmarks/search-intent-v2/run.py \
  --workspace /path/to/frozen-fetchcore --revision 32fb976 \
  --solr-url http://127.0.0.1:18983/solr --collection issue19 \
  --scenarios dogfood/scenarios/fetchcore-self.yaml \
  --label before --output /tmp/serving-before.json

PYTHONPATH=/path/to/after/src python benchmarks/search-intent-v2/run.py \
  --workspace /path/to/frozen-fetchcore --revision 32fb976 \
  --solr-url http://127.0.0.1:18983/solr --collection issue19 \
  --scenarios dogfood/scenarios/fetchcore-self.yaml \
  --label after --output /tmp/serving-after.json
```

The native integration fixtures are opt-in and remove only their own uniquely identified records/index:

```bash
FETCHCORE_INTENT_SOLR_CORE_URL=http://127.0.0.1:18983/solr/issue19 \
FETCHCORE_INTENT_OPENSEARCH_URL=http://127.0.0.1:19200 \
python -m pytest -q tests/test_search_intent_v2_integration.py
```

The unchanged frozen baseline protocol can be rerun for each source tree:

```bash
PYTHONPATH=/path/to/source/src python benchmarks/baseline-v1/run.py \
  --workspace /path/to/NeuroTrack_MS --arm local-lexical \
  --output /tmp/baseline-comparison
```

See the [SearchIntent contract](../concepts/search-intent.md) for the design, compatibility mapping, channel behavior and explicit unsupported policy.

## Implementation and verification inventory

The minimal model uses one expression tree rather than separate alternatives and conjunction registries. Legacy fields stay in the same dataclass; there is one reader/translator path, with deterministic compatibility behavior. The [contract document](../concepts/search-intent.md) specifies every field and backend/channel behavior.

| File | Responsibility |
| --- | --- |
| `src/fetchcore/retrieval/contracts.py` | Immutable canonical/structured intent, bounded declared expression, provenance for expansions, versioned reader and legacy serialization. |
| `src/fetchcore/retrieval/config.py` | Two capabilities: canonical query consumption and lexical requirements. |
| `src/fetchcore/retrieval/translation.py` | Typed translation observations, reject policy and opt-in existing response diagnostics. |
| `src/fetchcore/retrieval/embedding.py` | Canonical encoder input; unchanged legacy/document bytes; explicit rejection of unsupported structure/expansions. |
| `src/fetchcore/application/service.py` | Preserve the original query and check the receiving backend's capability. |
| `src/fetchcore/adapters/retrieval/local/backend.py` | Feed canonical text into existing matching, preserve it past the legacy term budget, reject unsupported structure, expose diagnostics. |
| `src/fetchcore/adapters/retrieval/solr/query.py` | Native eDisMax canonical query, required field/boolean filters and optional hints/expansions. |
| `src/fetchcore/adapters/retrieval/solr/backend.py` | Pre-execution gate and actual lexical/dense translation diagnostics. |
| `src/fetchcore/adapters/retrieval/opensearch/query.py` | Native multi-match canonical query, nested bool filters and optional hints/expansions. |
| `src/fetchcore/adapters/retrieval/opensearch/backend.py` | Pre-execution gate and lexical/dense diagnostics. |
| `src/fetchcore/adapters/retrieval/qdrant/backend.py` | Validate all participating channels; expose dense/sparse representations. |
| `src/fetchcore/adapters/retrieval/qdrant/sparse.py` | Pass canonical text through the existing sparse analyzer; reject groups instead of dropping them. |
| `src/fetchcore/baseline.py` | Use the canonical intent reader so benchmark input cannot silently lose v2 fields. |
| `tests/test_application_service.py` | Update the serving expectation and test exact original text plus unsupported-backend rejection. |
| `tests/test_search_intent_v2.py` | Serialization, invariants, invalid inputs, bounded structure, capability rejection, native requests, optional diagnostics and frozen v1 equivalence. |
| `tests/test_search_intent_v2_integration.py` | Real Solr/OpenSearch branch semantics, required anchors, escaping, native text analysis and optional hints. |
| `benchmarks/search-intent-v2/run.py` | Repeatable application-serving measurement on one immutable projection. |
| `benchmarks/search-intent-v2/artifacts/` | Raw before/after MCP and repeated serving observations, plus frozen local comparison evidence. |
| `docs/concepts/search-intent.md` | Canonical design, exact migration rules, backend matrix, bounds and limitations. |
| `docs/reference/contracts.md`, `docs/reference/capabilities.md` | Public contract/capability references. |
| `docs/architecture/retrieval.md`, `docs/README.md` | Current architectural status and discoverable documentation links. |
| `docs/benchmarks/search-intent-v2.md` | Hypothesis, measurements, regressions, commands and implementation report. |

Validation on the implementation: 341 tests passed with both native integration fixtures enabled; Ruff, strict mypy over `src`, documentation checks, and sdist/wheel build passed. The regular suite skips the two opt-in integration tests unless services are explicitly supplied. The all-channel frozen translation digest is a compatibility check, not a claim of measured v2 dense/hybrid retrieval quality.

No generic NLP, generic query parser, synonym engine, core stemming/tokenization, learned planner, automatic expansion, authority/satisfaction model or targeted fast path was added. Scope/configuration, provenance, persisted identities, BM25 weights, fusion and reranking boundaries remain separate.

The concrete follow-up discovered is reviewing incremental-sync dogfood Ground Truth against the moved implementation modules. The remaining miss and rank regression are retained in the evidence; they were not used to justify additional tuning here.
