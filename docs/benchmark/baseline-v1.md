# FetchCore baseline v1

Issue: #18

FetchCore baseline v1 freezes retrieval quality and operational cost before
SearchIntent v2, targeted fast paths, caching, adaptive budgets or reranking
promotion change retrieval behavior.

The baseline evaluates FetchCore retrieval only. It does not decide consumer
satisfaction or authority.

## Measurement layers

Keep three layers separate:

1. **telemetry** — observable runtime cost and flow;
2. **diagnostics** — why a candidate matched or how a backend translated a query;
3. **quality metrics** — comparison of returned evidence against explicit Ground Truth.

Runtime responses may carry optional `RetrievalTelemetry`. Ground Truth metrics
are computed only by `fetchcore.baseline`.

## Frozen v1 arms

| Arm | Backend | Mode |
| --- | --- | --- |
| local-lexical | local-semantic | lexical |
| solr-lexical | solr | lexical |
| solr-hybrid | solr | hybrid |
| opensearch-lexical | opensearch | lexical |
| opensearch-hybrid | opensearch | hybrid |
| qdrant-dense | qdrant-vector | dense |
| qdrant-hybrid | qdrant-vector | hybrid |

A runner validates each arm against `RetrievalBackendCapabilities`. Unsupported
capabilities fail closed; the protocol never substitutes another mode silently.

## Frozen run protocol

A promoted baseline run uses:

- one warm-up per query, excluded from measurement;
- at least 20 measured repetitions per query;
- the same repository revision and Ground Truth across compared arms;
- one controlled retrieval/configuration variable at a time when comparing later changes;
- explicit `k` and result budget;
- deterministic config/corpus identity in every artifact.

The implementation uses nearest-rank percentiles for p50/p95/p99.

## Corpus identity

Every artifact must identify the corpus sufficiently to reproduce it. At minimum:

```json
{
  "repository": "owner/repository",
  "revision": "<immutable revision>",
  "corpus_id": "<stable benchmark corpus id>"
}
```

For multi-repository experiments, record every repository/revision pair.

Ground Truth identifies minimal evidence regions by canonical repository path plus
1-based line ranges. It should cover representative source code/tests and, when
the corpus contains them, repository documentation, architecture/decision records,
schemas, configuration or workflows. Alternatives may encode multiple equally
sufficient evidence locations without forcing one arbitrary implementation.

A documentation/policy match is evidence. FetchCore does not promote it to
organizational authority.

## Retrieval telemetry

The backend-neutral response telemetry records:

- retrieval mode;
- end-to-end duration;
- candidates examined and returned;
- returned character count;
- bounded stage telemetry;
- relevant projection/serializer/fusion fingerprints;
- current result budget.

Current stages are:

- local lexical: `lexical`;
- Solr/OpenSearch dense: `embedding -> dense`;
- Solr/OpenSearch hybrid: `embedding -> dense -> lexical -> fusion`;
- Qdrant dense: `embedding -> dense`;
- Qdrant hybrid: `embedding -> dense -> lexical(sparse) -> fusion`.

The current backends return complete batches, so true time-to-first-candidate is
not observable yet and is omitted rather than fabricated.

Post-acquisition reranking reports its own `duration_ms` when it is explicitly
enabled. Reranking remains experimental and is not a frozen v1 arm.

## Indexing telemetry

`RepositoryIndexTelemetry` keeps its existing detailed coverage and now also
exports stable baseline aliases:

- `files_scanned`;
- `files_changed`;
- `chunks_total`;
- `chunks_changed`;
- `indexing_throughput_chunks_per_second`.

Backend index synchronization may emit `IndexSyncTelemetry`:

- total sync duration;
- logical knowledge writes/deletes;
- embedding requests;
- embedding vectors;
- embedding duration;
- embedding throughput.

Blue/green generation garbage collection is not counted as a logical
knowledge-document delete when the previous document count is not already
available. The metric must not add backend reads merely to manufacture parity.

## Quality metrics

Baseline v1 uses evidence-region Ground Truth because finding the right file is
not sufficient for a coding agent or LLM. Each query declares logical evidence
requirements. A requirement has one or more sufficient alternatives; each
alternative contains one or more minimal line regions.

The evaluator records:

- **source Recall@K** — diagnostic only: whether each evidence requirement had
  at least one source-complete alternative represented in the returned results;
- **EvidenceRecall@K** — primary: fraction of evidence requirements whose
  sufficient regions are fully covered by the top-K results;
- **SpanCoverage@K** — primary: mean best-alternative line coverage across
  evidence requirements, preserving partial recovery;
- **evidence MRR** — reciprocal rank of the first result that adds new reviewed
  Ground Truth line coverage;
- **evidence nDCG@K** — discounted incremental span-coverage gain by result
  rank, normalized against the same observed gains sorted into ideal order;
- **useful context line ratio** — unique reviewed Ground Truth lines covered
  from the best alternative for each requirement divided by all returned result
  lines. Duplicate returned spans still consume denominator budget;
- latency, candidates examined/returned, returned lines/chars and retrieval
  telemetry.

A `source_ref` hit with the wrong line range therefore improves source Recall
but does not improve EvidenceRecall or SpanCoverage.

Every top-K result persisted in an artifact carries only evaluation coordinates:

```text
rank
id
source_ref
line_start
line_end
```

Source content is not copied into the public artifact. These coordinates make
quality re-evaluation possible without querying a backend again.

## Artifact

Promoted retrieval artifacts use:

```text
schema_version = 2
protocol = fetchcore.baseline-v1
ground_truth_schema_version = 2
metrics_schema_version = 1
```

They also carry:

- arm identity;
- corpus identity;
- config identity and SHA-256 fingerprint;
- Ground Truth SHA-256 fingerprint;
- metric-definition SHA-256 fingerprint;
- all measured observations with ranked result spans;
- aggregate metrics;
- observed projection/serializer/fusion fingerprints.

The Ground Truth and metric fingerprints are comparison boundaries. A changed
query/evidence definition or metric formula is not silently comparable with the
frozen baseline. Such a change requires explicit protocol/schema evolution
rather than redefining `baseline-v1` in place after promotion.

The JSON shape is intentionally independent of retrievalgate. An external
regression harness can consume the artifact without FetchCore depending on it.

## Running locally

Index the target repository in the backend being measured first. Then configure
the same `SearchScope`, immutable corpus identity and Ground Truth for every
comparable arm.

Example using the local lexical backend:

```python
from pathlib import Path

from fetchcore.backends.local_semantic import LocalSemanticBackend
from fetchcore.baseline import (
    BaselineArm,
    BaselineQueryCase,
    BaselineRunner,
    write_baseline_artifact,
)
from fetchcore.search import SearchIntent, SearchScope

workspace = Path(".").resolve()
arm = BaselineArm("local-lexical", "local-semantic", "lexical")
scope = SearchScope(
    workspace=workspace,
    revision="<git-sha>",
    repository="owner/repository",
    knowledge_types=("code", "tests", "documentation", "configuration"),
    max_results=20,
)
cases = (
    BaselineQueryCase(
        query_id="q-auth-validation",
        intent=SearchIntent(semantic_terms=("authentication validation",)),
        relevant=("repository://owner/repository/src/auth.py",),
        identity_field="source_ref",
    ),
)

runner = BaselineRunner(
    backend=LocalSemanticBackend(),
    arm=arm,
    scope=scope,
    repetitions=20,
    warmups=1,
)
artifact = runner.run(
    cases,
    corpus={
        "corpus_id": "example-v1",
        "repository": "owner/repository",
        "revision": "<git-sha>",
    },
    config_identity={"protocol_config": "baseline-v1"},
)
write_baseline_artifact(
    Path("artifacts/baseline-v1/local-lexical.json"),
    artifact,
)
```

For Solr/OpenSearch/Qdrant, instantiate the corresponding backend and provide
the backend settings plus `RetrievalConfig` through `SearchScope`. The arm
mode must match the resolved retrieval mode or the runner fails closed.

Do not put API keys or credentials in `config_identity`.

## Promotion gate

A measurement is a frozen FetchCore baseline only when:

1. the corpus revision and evidence-region Ground Truth are immutable;
2. Ground Truth schema/fingerprint and metric schema/fingerprint match;
3. every requested arm actually ran with its declared capability;
4. every returned top-K result has observable line coordinates;
5. measured repetitions meet the protocol;
6. artifacts record the configuration/fingerprints;
7. failures are explicit rather than replaced with another backend/mode.

Schema fixtures and unit-test artifacts are not performance baselines.


## Frozen v1 corpus and executable suite

The first promoted baseline uses the versioned suite under:

```text
benchmarks/baseline-v1/
├── manifest.json
├── ground-truth.json
├── run.py
└── artifacts/
```

The frozen corpus identity is:

```text
repository: oalangomes/NeuroTrack_MS
revision: 74ce3244956a65baed61f1dae6f498cb2be4f3b7
corpus_id: neurotrack-ms-74ce3244
```

The corpus repository is private. FetchCore stores only query intent, evidence-region
coordinates and immutable repository identity. Source content is materialized
locally and must not be copied into benchmark artifacts.

The runner fails closed when:

- the corpus checkout is not at the exact frozen revision;
- the corpus checkout has uncommitted changes;
- a requested backend service cannot be reached;
- a requested backend capability is unsupported;
- Solr does not expose the expected frozen schema/vector contract.

Backend defaults are not relied on for corpus coverage. The manifest explicitly
freezes local file patterns, scan bounds, knowledge-type rules, collection/index
names, candidate window and embedding identity.

Run every arm against an already prepared environment:

```bash
python benchmarks/baseline-v1/run.py \
  --workspace /path/to/NeuroTrack_MS
```

To rebuild backend projections first:

```bash
python benchmarks/baseline-v1/run.py \
  --workspace /path/to/NeuroTrack_MS \
  --prepare-index
```

To measure one arm while diagnosing the environment:

```bash
python benchmarks/baseline-v1/run.py \
  --workspace /path/to/NeuroTrack_MS \
  --arm local-lexical
```

`--prepare-index` writes a separate `indexing.json` artifact so repository
acquisition/indexing cost is not mixed into repeated retrieval latency.

### Environment identity

Every measured artifact records the logical profile
`fetchcore-local-benchmark-v1` plus observed:

- Python version;
- OS/kernel release;
- machine architecture;
- CPU count;
- Solr/OpenSearch/Qdrant versions used by the selected arm;
- Ollama version for dense/hybrid arms;
- embedding provider/model/dimensions.

Quality metrics can be inspected across environments, but latency and throughput
must only be compared as equivalent when the recorded environment profile and
relevant service/runtime identities are compatible.
