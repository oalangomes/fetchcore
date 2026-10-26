# MongoDB Search and Vector Search

**Status: experimental adapter; live acceptance and comparative benchmarks pending.**
Backend ID: `mongodb-search`. This adapter addresses the implementation slices
of [issue #72](https://github.com/oalangomes/fetchcore/issues/72); passing the
offline contracts does not satisfy that issue's benchmark gate.

## Requirements and deployment

Install the optional driver:

```bash
python -m pip install 'fetchcore[mongodb]'
```

Use a MongoDB 8.0-or-newer deployment that explicitly supplies MongoDB Search,
Vector Search, and search-index management. Atlas and Search-enabled local
Atlas deployments use the same adapter. An ordinary Community MongoDB container
without the search service is **not** a supported substitute. For local
development, follow MongoDB's
[local Atlas deployment instructions](https://www.mongodb.com/docs/atlas/cli/current/atlas-cli-deploy-local/).
The adapter does not provision a database server, disable TLS, or infer local
capabilities from a connection URI. Authentication, network allowlists, and
search-index-management permissions are operator responsibilities.
Unacknowledged writes (`w=0`) and non-primary read preferences are rejected
because they cannot support the snapshot compare-and-swap/read checks.

Official protocol references:
[Search](https://www.mongodb.com/docs/atlas/atlas-search/),
[Vector Search](https://www.mongodb.com/docs/atlas/atlas-vector-search/),
and [hybrid search](https://www.mongodb.com/docs/atlas/atlas-vector-search/hybrid-search/).

## Operator configuration

Use the existing [runtime configuration boundary](runtime-configuration.md):

```yaml
version: 1
runtime:
  backend: mongodb-search
  repository: owner/repository
  workspace: /absolute/path/to/repository
backends:
  mongodb-search:
    database: fetchcore
    collection: repository_knowledge
    search_index: fetchcore-search
    vector_index: fetchcore-vector
    vector_dimensions: 768
    timeout_seconds: 10
    batch_size: 200
retrieval:
  dense:
    enabled: true
    embedding:
      provider: ollama
      model: embeddinggemma
      dimensions: 768
  hybrid:
    enabled: true
    strategy: rrf
embedding:
  ollama:
    url: http://localhost:11434
```

Inject a connection URI with `FETCHCORE_MONGODB_URI`. Both `mongodb://` and
`mongodb+srv://` are accepted. Do not commit credentials in YAML or shell
history. The resolved configuration masks the entire URI. Additional overrides
are `FETCHCORE_MONGODB_DATABASE`, `FETCHCORE_MONGODB_COLLECTION`,
`FETCHCORE_MONGODB_SEARCH_INDEX`, `FETCHCORE_MONGODB_VECTOR_INDEX`, and
`FETCHCORE_MONGODB_VECTOR_DIMENSIONS`. The adapter never discovers environment
variables itself. CLI, SDK, and MCP use the same composed backend.

Omit `retrieval` for lexical-only operation; no embedding provider is invoked.
Enable `dense` without `hybrid` for vector-only operation. Provider/model,
dimensions, serializer, projection, and indexing fingerprints must match the
persisted snapshot. Changing them requires an explicit rebuild; changing index
definitions also requires explicit operator replacement of those indexes.

## Index lifecycle

Bootstrap creates missing Search indexes, not a server. For example, use the
existing operational CLI with the same settings:

```bash
fetchcore index --workspace /absolute/path/to/repository \
  --repository owner/repository --backend mongodb-search \
  --backend-settings '{"database":"fetchcore","collection":"repository_knowledge"}' \
  bootstrap
```

Both operational CLI surfaces resolve runtime configuration and environment
overrides. Do not put a secret URI in `--backend-settings`. SDK composition is
also available:

```python
from fetchcore.application.runtime_config import load_runtime_config
from fetchcore.application.indexing import build_index_context

config = load_runtime_config()
context = build_index_context(
    workspace=config.workspace,
    repository="owner/repository",
    branch=None,
    revision=config.revision,
    backend=config.backend,
    backend_settings=dict(config.backend_settings),
    retrieval=config.retrieval,
)
try:
    print(context.legacy("bootstrap"))
finally:
    context.store.transport.close()
```

Index creation is asynchronous. Bootstrap reports `building` and never waits
silently or declares readiness. Repeat `fetchcore doctor --json` explicitly
until the configured definitions are `READY` and queryable. The Search index
uses static Projection v3 field mappings; the vector index uses cosine
similarity and indexed repository/scope filters. Snapshot controls are excluded
from both acquisition channels.

Once indexes are ready, the registered repository indexing commands accept
`--config`/`FETCHCORE_CONFIG` and `mongodb-search`; see
[repository indexing](repository-indexing.md). Sync uses Git deltas where safe,
compares projected records, embeds/writes changed records only, and deletes
removed records only within the bound repository. Embedding requests are
bounded by `batch_size`; persistence currently issues individual record writes.
Unchanged content retains its original document revision; results also expose
the active snapshot revision.

Snapshots use compare-and-swap tokens. A write marks the repository unservable
until successful promotion. Failed writes leave a `failed` snapshot and require
explicit rebuild, never a silent rollback or fallback. Readers reject snapshot
changes during acquisition. A process crash can leave a `writing` control
record: stop/verify all writers before operator recovery of that repository's
control record; automatic lock stealing is intentionally unsupported.

Search indexing is eventually consistent, independently of durable document
writes and index-definition readiness. Sync responses and search responses
explicitly report `search_consistency: eventual`. A current canonical snapshot
is not proof that all recent mutations are visible to Search/Vector Search.
The adapter does not promise an atomic search cutover or read-your-writes.
Use the live acceptance test to wait explicitly for test-document visibility.
`readiness` reports durable snapshot/currentness and schema facts, not an
independent guarantee of search-index visibility.

## Retrieval behavior and limits

- Lexical acquisition uses `$search`, typed Projection v3 channels, exact token
  fields, optional hints/expansions, and required boolean/anchor filters.
- Targeted exact lookup uses the lexical path and a two-result ambiguity
  window; it never embeds the query.
- Vector acquisition uses `$vectorSearch`; embeddings come only through
  `EmbeddingProvider`. Candidate exploration is bounded by
  `min(10000, 20 * requested_candidates)`. This is an initial adapter budget,
  not a measured quality improvement.
- Hybrid runs lexical and vector acquisition independently in parallel, then
  uses the existing deterministic FetchCore RRF kernel. Deduplication retains
  both acquisition paths. MongoDB native fusion is not used.
- Repository, exact repository paths, knowledge type, language and source-type
  scope are enforced in the backend queries. Path scope is exact, not a glob or
  directory-prefix search.
- Required anchors/boolean expressions are supported in lexical mode. As in
  other current dense channels, dense/hybrid requests with these requirements
  are rejected before I/O rather than silently broadening.
- `max_results` is bounded by 5000. `diagnostic_candidate_window` selects the
  bounded acquisition window. One extra hit observes truncation; it is not
  passed to fusion. Metadata distinguishes output and candidate-window
  truncation. Scores remain retrieval observations, never authority.

## Validation and benchmark gate

Offline contract tests verify translation shapes, capabilities, provenance,
incremental writes/deletes, fingerprint incompatibility, async readiness,
failure propagation, runtime precedence, and secret redaction:

```bash
python -m pytest -q tests/contract/repository/test_mongodb_search.py
```

For an isolated live smoke collection on either supported deployment:

```bash
python -m pytest -q -m mongodb_integration \
  tests/integration/retrieval/test_mongodb_search_integration.py
```

Set `FETCHCORE_MONGODB_TEST_URI` through your secret environment first;
`FETCHCORE_MONGODB_TEST_DATABASE` defaults to `fetchcore_test`. The test creates
and drops only its uniquely named smoke collection. Its constant three-dimensional
embedding fixture checks protocol execution, **not retrieval quality**.

Live validation and relevance/latency measurements have not been executed for
this adapter. No Recall@K, MRR/nDCG, p50/p95/p99, throughput, cost, or footprint
claims are made. Before removing experimental status or closing #72, compare
MongoDB lexical/dense/FetchCore-RRF hybrid against the existing frozen baseline
using the same corpus revision, queries, ground truth, embedding model,
projection fingerprints, and candidate/output budgets. Record the metrics
and regressions required by the [benchmark protocol](../benchmarks/protocol.md).
