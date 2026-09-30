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

Ground Truth may identify results by canonical result `id` or `source_ref`.
It should cover representative source code/tests and, when the corpus contains
them, repository documentation, architecture/decision records, schemas,
configuration or workflows.

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

For every measured query/run, baseline v1 records:

- Recall@K;
- Ground Truth recovered / total;
- reciprocal rank;
- nDCG@K using binary relevance;
- useful evidence density = relevant returned results / returned results;
- latency;
- candidates examined/returned;
- returned characters;
- retrieval telemetry.

The aggregate artifact records mean quality/efficiency metrics and repeated-run
p50/p95/p99 latency.

## Artifact

Artifacts use:

```text
schema_version = 1
protocol = fetchcore.baseline-v1
```

They also carry:

- arm identity;
- corpus identity;
- config identity and SHA-256 fingerprint;
- all measured observations;
- aggregate metrics;
- observed projection/serializer/fusion fingerprints.

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

1. the corpus revision and Ground Truth are immutable;
2. every requested arm actually ran with its declared capability;
3. measured repetitions meet the protocol;
4. artifacts record the configuration/fingerprints;
5. failures are explicit rather than replaced with another backend/mode.

Schema fixtures and unit-test artifacts are not performance baselines.
