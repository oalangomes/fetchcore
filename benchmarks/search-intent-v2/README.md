# SearchIntent v2 benchmark

Evidence-aware benchmark for issue #19. It compares the legacy query representation with `canonical_query` while keeping corpus, Ground Truth, backend configuration, indexes, budgets and retrieval mechanics fixed.

Protocol:

```text
fetchcore.search-intent-v2-evidence
```

The frozen `fetchcore.baseline-v1` remains unchanged. This benchmark reuses its immutable NeuroTrack_MS corpus, reviewed evidence Ground Truth and shared evidence evaluators.

## What is compared

For the same reviewed query string `Q`:

```python
SearchIntent(semantic_terms=(Q,))
```

versus:

```python
SearchIntent(canonical_query=Q)
```

Only the SearchIntent representation changes. Do not change ranking weights, BM25 configuration, RRF parameters, embedding model, chunking, projection, candidate budgets, Ground Truth or query text between the paired representations.

## Supported arms

```text
local-lexical

solr-lexical
solr-dense
solr-hybrid

opensearch-lexical
opensearch-dense
opensearch-hybrid

qdrant-dense
qdrant-hybrid
```

Qdrant lexical is intentionally absent because that capability is not supported. No fallback substitutes another mode.

## Frozen corpus

Use the exact frozen NeuroTrack_MS checkout:

```text
repository: oalangomes/NeuroTrack_MS
revision: 74ce3244956a65baed61f1dae6f498cb2be4f3b7
```

The workspace must be clean and exactly at that revision.

Ground Truth:

```text
benchmarks/baseline-v1/ground-truth.json
```

Canonical query set:

```text
benchmarks/search-intent-v2/evidence-queries.json
```

The suite contains 20 reviewed queries and 42 evidence requirements. Do not tune canonical queries after seeing benchmark results.

## Services and embeddings

Remote arms use the service endpoints configured by:

```text
benchmarks/baseline-v1/manifest.json
```

Dense/hybrid identity:

```text
provider: ollama
model: embeddinggemma
dimensions: 768
```

The full remote matrix requires Solr, OpenSearch, Qdrant and Ollama.

## Smoke run

Validate all nine arms with one measured repetition before the promoted run:

```bash
python benchmarks/search-intent-v2/evidence.py \
  --workspace /path/to/NeuroTrack_MS \
  --compare-representations \
  --warmups 1 \
  --repetitions 1 \
  --output benchmarks/search-intent-v2/artifacts/evidence-smoke
```

With no `--arm`, all nine supported arms run.

A reduced run must report:

```json
{
  "full_protocol": false
}
```

It is a smoke/diagnostic artifact, not the promoted result.

## Reusing existing indexes

If compatible Solr/OpenSearch/Qdrant snapshots already exist, run **without** `--prepare-index`.

The runner validates snapshot revision and applicable projection/serializer fingerprints. Legacy and canonical must use the same persisted snapshot.

Never rebuild between the two representations.

If snapshots are missing, stale or incompatible, prepare them once:

```bash
python benchmarks/search-intent-v2/evidence.py \
  --workspace /path/to/NeuroTrack_MS \
  --compare-representations \
  --prepare-index \
  --warmups 1 \
  --repetitions 1 \
  --output benchmarks/search-intent-v2/artifacts/evidence-smoke
```

`--prepare-index` prepares each selected remote backend once for all required modes. Indexing metrics remain separate from query-quality metrics.

## Full promoted protocol

After the nine-arm smoke succeeds:

```bash
python benchmarks/search-intent-v2/evidence.py \
  --workspace /path/to/NeuroTrack_MS \
  --compare-representations
```

Promoted defaults:

```text
arms:          9
representations: legacy + canonical
queries:       20
warmups:       1
repetitions:   20
K:             20
max_results:   20
diagnostic candidate window: 80
```

A complete promoted run must finish with:

```json
{
  "status": "complete",
  "full_protocol": true
}
```

Anything else is partial.

## Run selected arms

`--arm` is repeatable.

Solr hybrid:

```bash
python benchmarks/search-intent-v2/evidence.py \
  --workspace /path/to/NeuroTrack_MS \
  --arm solr-hybrid \
  --compare-representations
```

OpenSearch dense:

```bash
python benchmarks/search-intent-v2/evidence.py \
  --workspace /path/to/NeuroTrack_MS \
  --arm opensearch-dense \
  --compare-representations
```

Qdrant hybrid:

```bash
python benchmarks/search-intent-v2/evidence.py \
  --workspace /path/to/NeuroTrack_MS \
  --arm qdrant-hybrid \
  --compare-representations
```

Several arms:

```bash
python benchmarks/search-intent-v2/evidence.py \
  --workspace /path/to/NeuroTrack_MS \
  --arm solr-lexical \
  --arm solr-dense \
  --arm solr-hybrid \
  --compare-representations
```

## Run one representation

For diagnostics:

```bash
python benchmarks/search-intent-v2/evidence.py \
  --workspace /path/to/NeuroTrack_MS \
  --arm solr-hybrid \
  --representation legacy
```

or:

```bash
python benchmarks/search-intent-v2/evidence.py \
  --workspace /path/to/NeuroTrack_MS \
  --arm solr-hybrid \
  --representation canonical
```

The promoted comparison uses `--compare-representations`.

## Artifacts

Default output:

```text
benchmarks/search-intent-v2/artifacts/evidence/
```

Each execution creates a new version:

```text
V0.1/
V0.2/
V0.3/
...
```

A paired run contains files similar to:

```text
V0.N/
├── run.json
├── queries.json
├── comparison.json
├── README.md
├── local-lexical-legacy.json.gz
├── local-lexical-canonical.json.gz
├── solr-lexical-legacy.json.gz
├── solr-lexical-canonical.json.gz
├── ...
└── indexing.json
```

`indexing.json` exists only when index preparation was requested.

`run.json` records protocol status and provenance. `README.md` is the generated human-readable report. `comparison.json` contains paired deltas and per-query classifications.

## Quality metrics

The benchmark reuses the baseline evidence evaluator:

```text
SourceRecall@20
EvidenceRecall@20
SpanCoverage@20
EvidenceMRR
EvidenceNDCG@20
UsefulContextLineRatio
```

Budget-aware metrics:

```text
ContextYield@100
ContextYield@250
ContextYield@500
ContextYield@1000
```

Operational metrics include zero-result rate, candidates examined/returned, returned lines/chars and p50/p95/p99 latency.

Time-to-first-candidate is not currently available from runtime telemetry and is not synthesized.

## Translation diagnostics

Translation diagnostics stay separate from retrieval quality:

```text
channel
translation status
canonical_query_consumed
unsupported_required
unsupported_optional
expansions supplied/consumed
hints supplied/consumed
signals supplied/consumed
```

They explain how a channel handled an intent. They are not satisfaction or authority.

## Reading deltas

```text
delta = canonical - legacy
```

A positive EvidenceRecall delta means canonical recovered more reviewed evidence.

Results remain visible per backend/mode. Do not collapse arms into one score that hides regressions.

## Failed runs

A failed execution leaves a versioned `run.json` with:

```json
{
  "status": "failed"
}
```

Do not edit a failed artifact to make it appear complete. Fix the cause and start a new versioned execution.

## Current checkpoint

The committed `artifacts/evidence/V0.1` is a partial smoke:

```text
arm: local-lexical
representations: legacy + canonical
repetitions: 1
full_protocol: false
```

It is not the final issue #19 result.

A previous `artifacts/evidence-smoke/V0.1` attempt prepared remote indexes for all backend families but failed during validation. Reuse those snapshots only if the current runner validates them; otherwise run again with `--prepare-index`.

## Related documentation

Experiment design and interpretation:

```text
docs/benchmarks/search-intent-v2.md
```

Frozen evidence ruler:

```text
docs/benchmarks/baseline-v1.md
```

FetchCore evaluates evidence acquisition only:

```text
retrieval != satisfaction != authority
```
