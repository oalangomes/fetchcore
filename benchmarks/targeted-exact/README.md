# Targeted exact retrieval experiment

Issue #34 measures `RetrievalConfig(targeted="exact_unique")` against
`targeted="disabled"` through the same application service. `disabled` is normal
retrieval with the **original SearchIntent**, including its required path or
`source_ref` anchor. A unique exact hit is a bounded acquisition path. Any
fallback also sends the original intent through the existing channel translation
policy; unsupported hard semantics fail closed.

## Reproduction and controls

Use a clean checkout of `oalangomes/NeuroTrack_MS` at
`74ce3244956a65baed61f1dae6f498cb2be4f3b7`:

```bash
python benchmarks/targeted-exact/run.py \
  --workspace /path/to/frozen/NeuroTrack_MS \
  --query medication-update-owner-service \
  --query architecture-overview \
  --repetitions 20 --warmups 1 \
  --output /tmp/targeted-canonical.json
```

Repeat with `--representation legacy` and a distinct output path. Canonical
queries come from `search-intent-v2/evidence-queries.json`; original terms and
reviewed Ground Truth come from `baseline-v1/ground-truth.json`. Each of the two
selected queries has one reviewed source and is tested with both anchor kinds.
The representations are separate experiments. Corpus, revision, Ground Truth,
query, anchor, backend settings, K/result limit 20, one warmup, 20 repetitions,
source/runner fingerprints and acquisition metrics remain fixed within each
policy pair. Policy order alternates by iteration. The frozen baseline files
were not changed.

## Corrected controlled results

[Canonical observations](artifacts/canonical.json.gz), [legacy observations](artifacts/legacy.json.gz),
and [comparison with checksums](artifacts/comparison.json) were generated after
the fallback correction. Each representation has 80 observations per policy.

| Measurement | Canonical disabled | Canonical exact unique | Legacy disabled | Legacy exact unique |
| --- | ---: | ---: | ---: | ---: |
| Successful / rejected | 0 / 80 | 80 / 0 | 0 / 80 | 80 / 0 |
| EvidenceRecall@20 | not measured | 1.000 | not measured | 1.000 |
| Candidate records examined, mean | not acquired | 1 | not acquired | 1 |
| Files read, mean | not acquired | 1 | not acquired | 1 |
| Broad calls, mean | 0 | 0 | 0 | 0 |
| Wall p50, ms (successful only) | not measured | 8.464 | not measured | 8.443 |
| Wall p95, ms (successful only) | not measured | 10.034 | not measured | 9.982 |
| Returned lines, mean | not returned | 87.5 | not returned | 87.5 |
| Returned chars, mean | not returned | 3,917 | not returned | 3,917 |

All disabled observations were rejected with `unsupported_semantics` before
backend acquisition: the local lexical backend cannot preserve the required
anchor. This is the correct baseline semantics, not zero recall. The exact arm
returned a unique source in every measured case, with one candidate/file and no
broad call. Its bounded source prefix contains the reviewed regions for these
two queries. These data do not establish a relative quality, latency or cost
improvement over a *successful* disabled retrieval, since that control is
unsupported. Rejection wall times are recorded separately in raw observations.
The larger returned context is a cost of exact source acquisition. Each
query/signal cell has only 20 samples; p99 is retained for audit but is not a
reliable tail estimate. No time-to-first-candidate or cross-representation
performance claim is inferred.

## Broad retrieval equivalence

The [broad-equivalence artifact](artifacts/broad-equivalence.json) confirms
**20/20 original unanchored queries** equivalent to frozen V0.1 for ranked
IDs/spans, EvidenceRecall, SourceRecall, candidates examined, returned lines and
returned chars. Timing is excluded from deterministic equivalence. Reproduce:

```bash
python benchmarks/targeted-exact/check_broad_equivalence.py \
  --workspace /path/to/frozen/NeuroTrack_MS
```

## Failure and dense-avoidance controls

Missing `path` and `source_ref` lookups fall back with the original intent and
then reject `unsupported_semantics` on the local channel. Traversal is rejected
before lookup. A dense-mode unique-hit control records zero broad calls; it
measures avoidance only, not dense latency. Native Solr and OpenSearch tests
cover exact hits, lexical fallback, revisions and checksums. A dense fallback
with a required anchor rejects before constructing an embedder.

Run the native tests against disposable services:

```bash
FETCHCORE_TARGETED_SOLR_URL=http://127.0.0.1:8983/solr \
FETCHCORE_TARGETED_SOLR_COLLECTION=fetchcore-knowledge \
python -m pytest -q tests/integration/retrieval/test_targeted_integration.py -m solr_integration

FETCHCORE_TARGETED_OPENSEARCH_URL=http://127.0.0.1:9200 \
python -m pytest -q tests/integration/retrieval/test_targeted_integration.py -m opensearch_integration
```

## Scope

Local, Solr and OpenSearch implement exact lookup; Qdrant does not in this
slice. Bounded source prefixes may omit later evidence, and multiple chunks
are ambiguous. Two query identities do not support a general retrieval-quality
claim. Identifier/symbol/route lookup, Qdrant exact lookup, routing, graph,
hybrid tuning, reranking and adaptive budgets remain out of scope. Exact lookup
provides evidence; it does not decide satisfaction or authority. See the
[targeted retrieval contract](../../docs/concepts/targeted-retrieval.md).
