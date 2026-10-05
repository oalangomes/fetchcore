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

The first paired run produced the following quality measurements. No Ground Truth was changed to improve the result.

| Metric | Before | After |
| --- | ---: | ---: |
| Expected-path hit@10 | 1/6 (16.67%) | 5/6 (83.33%) |
| SourceRecall@10 | 0.0833 | 0.8333 |
| MRR | 0.1667 | 0.3889 |
| Zero-result rate | 83.33% | 0% |
| Mean candidates examined | 0.1667 | 10 |

The existing MCP smoke independently reproduced those expected-path and MRR results: [before](../../benchmarks/search-intent-v2/artifacts/mcp-before.json), [after](../../benchmarks/search-intent-v2/artifacts/mcp-after.json). The full repeated [before observations](../../benchmarks/search-intent-v2/artifacts/serving-before.json) preserve every measured rank, path and latency.

The authority-boundary scenario regressed from rank 1 to rank 2 even though aggregate MRR improved. The incremental-sync scenario still misses the reviewed top-10 paths. Its expected paths point to compatibility modules; implementation modules moved during earlier decomposition. That Ground Truth should be reviewed separately, not changed inside this experiment to raise the score.

Local lexical retrieval still uses its existing phrase/term matcher; no general improvement in natural-language local recall is claimed. Dense/hybrid requests with declared groups or anchors now explicitly reject rather than attempting a lossy representation. Qualified-symbol matching depends on values actually present in the projection; this is not a symbol resolver.

## Compatibility gate

All 20 frozen baseline-v1 intents produce byte-equivalent canonical mappings, Solr requests, OpenSearch requests, local query representations, sparse terms and embedding text. The aggregate JSON SHA-256 captured independently from baseline code is:

```text
4a3b18e742c447e878b1bf35ef4b78370c98e1f1db5b110269ae04c4882ccc12
```

The [compatibility test](../../tests/test_search_intent_v2.py) checks that digest against the new implementation. Document serializer/projection fingerprints and embedding identities were not changed. The existing frozen baseline runner is also exercised on the exact NeuroTrack corpus revision for the local lexical arm. Remote dense/hybrid quality is not re-promoted by these measurements; their v1 input representations are covered by the equivalence gate, and unsupported v2 groups fail before execution.

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
