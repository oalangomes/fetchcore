# Targeted exact retrieval experiment

Issue #34 compares `RetrievalConfig(targeted="exact_unique")` against `disabled`
through the same application service. Only this policy changes within each pair.
Hypothesis: a declared exact source identity avoids corpus-wide acquisition while
preserving or increasing reviewed evidence recovery.

The disabled arm executes the current broad backend search and the same exact
post-filter, so a required anchor never becomes an optional hint. Backend search
algorithms, RRF, projection/indexing and embeddings are unchanged.

## Reproduction and controls

Use a clean checkout of `oalangomes/NeuroTrack_MS` at
`74ce3244956a65baed61f1dae6f498cb2be4f3b7`. No services/embeddings are required for
the measured local arm.

```bash
python benchmarks/targeted-exact/run.py \
  --workspace /path/to/frozen/NeuroTrack_MS \
  --query medication-update-owner-service \
  --query architecture-overview \
  --repetitions 20 --warmups 1 \
  --output /tmp/targeted-canonical.json
```

Add `--representation legacy` for the original baseline term representation.
Canonical query strings come unchanged from `search-intent-v2/evidence-queries.json`;
legacy terms and reviewed evidence come unchanged from `baseline-v1/ground-truth.json`.
The two representations are separate experiments, never mixed within a pair.

The runner defaults to all single-source Ground Truth cases. The recorded run
selects one implementation query and one documentation query, each exercised
with both path and source-reference anchors. Selection is explicit in the
command, and omitted cases are recorded. This is a narrow targeted experiment,
not promotion of the full nine-arm #18/#19 protocol.

Controls held constant per pair: corpus/revision, query/anchor/origin, Ground
Truth, backend settings (copied from frozen V0.1 local artifact), result limit
and K=20, namespace, line/byte bounds. Local has no embedding/projection identity;
these are represented as null. One warmup per variant and 20 measured repetitions
produce 80 observations per policy. Policy order alternates each iteration.

Artifacts preserve full query mappings, ranked spans, existing evidence metric
observations, wall duration, targeted time, candidates, file reads, returned
lines/chars, fallback telemetry, source/runner fingerprints and runtime environment.
Quality uses the existing `fetchcore.baseline.evaluate_retrieval` evidence ruler.
Neither frozen baseline directory was modified. No regression-gate semantics are
implemented here.

The canonical artifact was measured with runtime commit
`17846ee0377b8f77f74de9eb55bfaeb811f9da99` and the runner from that commit. Later
runner changes add the selectable legacy representation and source-drift guard;
the artifact retains the actual runner hash used at measurement time.

## Canonical-query measurements

Raw paired observations: [canonical.json.gz](artifacts/canonical.json.gz).

| Metric | Broad, disabled policy | Targeted, exact unique |
| --- | ---: | ---: |
| EvidenceRecall@20 mean | 0.000 | 1.000 |
| Candidate records examined, mean | 0 | 1 |
| Repository files read, mean | 964 | 1 |
| Broad search calls per query | 1 | 0 |
| Wall p50, ms | 12,420.009 | 29.374 |
| Wall p95, ms | 16,911.190 | 43.226 |
| Targeted stage mean, ms | 0.483 | 28.391 |
| Returned lines, mean | 0 | 87.5 |
| Returned characters, mean | 0 | 3,917 |
| Fallback rate | 100% (policy disabled) | 0% |

The broad local canonical queries returned no candidates in these selected
cases, consistent with the limitations of that channel observed in #19. This
run demonstrates evidence recovery and less file acquisition, **not** a decrease
in candidate records (0 becomes 1). It also returns more context. Do not describe
it as a reduction in returned tokens/chars. The richer source prefix contains
all reviewed evidence for these two cases; that does not generalize to long
files beyond the prefix bound.

Each query/signal cell has 20 samples; the table combines 80 observations from
four cells. p95 is descriptive. Raw nearest-rank p99 is retained for audit but
is not a reliable tail estimate at this sample size. No time-to-first-candidate
is fabricated. Measurements are from one host, not a remote backend latency
comparison or a general claim about all repositories.

## Legacy-query measurements

The companion [legacy artifact](artifacts/legacy.json.gz) uses the **unchanged
original baseline terms** and identical paired policy controls. The
[comparison artifact](artifacts/comparison.json) collects both experiments,
checksums and per-query summaries. Runtime source fingerprints match in both.

| Metric | Broad, disabled policy | Targeted, exact unique |
| --- | ---: | ---: |
| EvidenceRecall@20 mean | 0.000 | 1.000 |
| Candidate records examined, mean | 9.5 | 1 |
| Repository files read, mean | 964 | 1 |
| Broad search calls per query | 1 | 0 |
| Wall p50, ms | 6,253.646 | 13.575 |
| Wall p95, ms | 8,168.383 | 24.646 |
| Targeted stage mean, ms | 0.188 | 15.180 |
| Returned lines, mean | 2.5 | 87.5 |
| Returned characters, mean | 90.5 | 3,917 |
| Fallback rate | 100% (policy disabled) | 0% |

Here candidate records as well as file reads decrease. Original broad lexical
snippets do not cover complete required evidence regions; exact source prefixes
do. The larger returned context is a measured tradeoff, not an efficiency win
on every axis. These averages cover 80 samples per policy, but only two distinct
queries. The same sample-size/tail and generalization limitations apply.
Differences between canonical and legacy runs are not used as a representation
performance claim: only within-run policy pairs are controlled.

## Broad retrieval equivalence

[The recorded check](artifacts/broad-equivalence.json) re-executes **all 20**
original unanchored legacy queries on the frozen corpus. Ranked result spans/IDs,
EvidenceRecall, source recall, candidates examined, returned characters and
returned line counts equal V0.1 for every query. Timing is deliberately excluded
from deterministic equivalence. Reproduce with:

```bash
python benchmarks/targeted-exact/check_broad_equivalence.py \
  --workspace /path/to/frozen/NeuroTrack_MS
```

## Failure and dense-avoidance controls

Both missing `path` and missing `source_ref` execute broad retrieval once, then
return no unrelated evidence after the exact post-filter. Traversal `../outside.py`
is rejected before lookup or broad acquisition. The raw artifact contains their
telemetry and call counts.

A separate control selects dense mode and asserts zero broad calls on a unique
exact hit. It is a call-count control, not a simulated dense latency measurement.
Native Solr/OpenSearch integration tests additionally use an embedding-factory
tripwire: unique exact hits succeed without creating the embedder, while missing
keys attempt the configured dense path. They test lexical fallback, revision
mismatch, canonical identity and checksum preservation against real services.

Run native tests against disposable services only:

```bash
FETCHCORE_TARGETED_SOLR_URL=http://127.0.0.1:18983/solr \
FETCHCORE_TARGETED_SOLR_COLLECTION=fetchcore34 \
python -m pytest -q tests/test_targeted_integration.py -m solr_integration

FETCHCORE_TARGETED_OPENSEARCH_URL=http://127.0.0.1:19200 \
python -m pytest -q tests/test_targeted_integration.py -m opensearch_integration
```

## Scope and interpretation

For the measured exact queries, targeted acquisition recovers more reviewed
evidence with fewer file reads and lower latency. Broad retrieval remains the
observable fallback. This conclusion is limited to the measured local cases;
remote adapters have semantic integration evidence, not performance promotion.

Qdrant exact lookup, identifiers, qualified symbols, routes, graph, a full router,
hybrid tuning (#84), RRF changes, reranking, query expansion and adaptive budgets
remain out of scope. See [the contract](../../docs/concepts/targeted-retrieval.md)
for bounded-prefix, multi-chunk ambiguity and post-filter underfill limitations.
