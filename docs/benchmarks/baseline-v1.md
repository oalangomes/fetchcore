# FetchCore baseline v1

Issue: #18

FetchCore baseline v1 is the frozen retrieval-quality and operational-cost ruler
used before evolving SearchIntent semantics, targeted fast paths, caches,
adaptive budgets, reranking, chunking or graph expansion.

The baseline evaluates retrieval evidence. It does not decide consumer
satisfaction or authority.

> Status: **frozen baseline (#18 closed)**. The evidence-aware nine-arm V0.1
> run was captured on 2026-10-01. This protocol remains the comparison contract;
> later experiments must identify their own protocol and execution versions.

## Why evidence regions

Finding the right file is not sufficient for a coding agent or LLM. A result may
have the expected `source_ref` but contain the wrong chunk.

Baseline v1 therefore models Ground Truth as logical evidence requirements:

```text
query
└── required_evidence[]          # requirements are AND
    └── alternatives[]           # alternatives are OR
        └── regions[]
            ├── path
            ├── line_start
            └── line_end
```

Each region is the smallest reviewed source span needed to contribute that piece
of technical evidence. Alternatives allow more than one equally sufficient
realization without forcing one arbitrary implementation.

The private corpus content is never copied into FetchCore. Only repository
identity, query intent, paths and 1-based line coordinates are versioned.

## Frozen corpus

```text
repository: oalangomes/NeuroTrack_MS
revision: 74ce3244956a65baed61f1dae6f498cb2be4f3b7
corpus_id: neurotrack-ms-74ce3244
```

The suite currently contains 20 reviewed queries and 42 evidence requirements
covering services, controllers, tests, routes, queues/workers, configuration,
Docker/runtime topology, architecture documentation and repository development
rules.

At runtime the runner fails closed if the checkout is not at the exact revision,
is dirty, or any Ground Truth region points outside the actual file length.

## Frozen arms

| Arm | Backend | Mode |
| --- | --- | --- |
| local-lexical | local-semantic | lexical |
| solr-lexical | solr | lexical |
| solr-dense | solr | dense |
| solr-hybrid | solr | hybrid |
| opensearch-lexical | opensearch | lexical |
| opensearch-dense | opensearch | dense |
| opensearch-hybrid | opensearch | hybrid |
| qdrant-dense | qdrant-vector | dense |
| qdrant-hybrid | qdrant-vector | hybrid |

Unsupported capabilities, incompatible schemas or environment failures are
explicit errors. No arm silently substitutes another mode.

The executable runner reads this matrix from `manifest.json`. Backend-specific
settings, service probes and index preparation live behind a small benchmark
adapter registry, so future suites can add a backend without adding conditional
branches to arm selection, measurement or reporting. The frozen v1 matrix is
not expanded in place; a new arm requires a new protocol version or an
explicitly experimental suite.

## Run protocol

A promoted run uses:

- one warm-up per query, excluded from measurement;
- 20 measured repetitions per query;
- the same immutable corpus and Ground Truth across all arms;
- K=20 and max 20 returned results;
- diagnostic candidate window 80 where supported;
- Ollama + `embeddinggemma` + 768 dimensions for dense paths;
- RRF as the hybrid fusion baseline;
- nearest-rank p50/p95/p99;
- one controlled retrieval/configuration variable at a time in later experiments.

## Quality metrics

### SourceRecall@K — diagnostic

For each evidence requirement, this asks whether at least one sufficient
alternative has all of its source files represented in the top K.

A source hit does **not** imply the evidence span was returned.

### EvidenceRecall@K — primary

```text
fully covered evidence requirements
───────────────────────────────────
all evidence requirements
```

A requirement is recovered only when one sufficient alternative is completely
covered by the union of returned result spans.

### SpanCoverage@K — primary

For each requirement, calculate line coverage for every alternative, keep the
best alternative, then average those coverage ratios across requirements.

This preserves partial recovery instead of collapsing every incomplete result to
zero.

### Evidence MRR

Reciprocal rank of the first returned result that adds reviewed Ground Truth line
coverage. It answers how soon useful evidence starts appearing.

### Evidence nDCG@K

Each result rank receives the incremental mean span-coverage gain created when
that result is added. DCG discounts those gains by rank. IDCG sorts the same
observed gains into ideal order.

This metric evaluates ordering of the evidence that was recovered.
Missing evidence is captured separately by EvidenceRecall and SpanCoverage.

### UsefulContextLineRatio — evidence-density diagnostic

```text
unique reviewed GT lines covered
────────────────────────────────
all returned result lines
```

Ground Truth lines are deduplicated. Returned spans are not: returning the same
chunk twice consumes context twice and therefore increases the denominator
without increasing useful evidence.

This is deliberately a line-based v1 measure because line coordinates are
backend-neutral and can be persisted without source content. Returned characters
are also recorded as an operational context-size metric.

`UsefulContextLineRatio` is a **density diagnostic over the complete returned
set**. It does not answer how much evidence fits inside a bounded consumer context.

### Derived ContextYield@B — budget-aware, outside the frozen v1 metric schema

Budget-aware evidence evaluation is intentionally versioned as a separate derived
artifact rather than silently changing baseline-v1 `metrics_schema_version = 1`.

For a line budget `B`, ranked result spans are consumed as a prefix:

```text
ranked spans
    ↓
pack whole spans in rank order
    ↓
stop before first overflow
    ↓
evaluate existing evidence requirements
```

No returned span is partially sliced to fit the budget.

The initial fixed cutoffs are 100, 250, 500 and 1000 lines. The derived artifact
reports:

- ContextYield@B: existing mean evidence-region span coverage inside the packed prefix;
- budgeted EvidenceRecall@B;
- consumed lines;
- packed result count.

Because baseline-v1 already persists ranked result coordinates, compatible frozen
runs can be re-evaluated offline without querying a backend. The derived metric
definition and budget configuration have independent fingerprints and fail closed
against incompatible Ground Truth/source-metric identities.

## Performance and cost metrics

Every measured observation also records:

- end-to-end duration;
- candidates examined and returned;
- returned lines and characters;
- bounded stage telemetry;
- projection/serializer/fusion fingerprints;
- current result budget.

Repeated observations produce p50/p95/p99 end-to-end latency.

Indexing is measured separately and never folded into query latency. Indexing
telemetry includes files/chunks observed, sync duration, logical backend writes
and deletes, embedding requests/vectors, embedding duration and throughput.

## Re-evaluable artifacts

Every top-K result persists coordinates only:

```text
rank
id
source_ref
line_start
line_end
```

No private source content is stored.

These coordinates allow metric bugs or reporting changes to be investigated
without querying the backend again, provided the Ground Truth and metric
semantics are unchanged.

Promoted retrieval artifacts use:

```text
schema_version = 2
protocol = fetchcore.baseline-v1
ground_truth_schema_version = 2
metrics_schema_version = 1
```

They also contain SHA-256 fingerprints for:

- Ground Truth;
- metric definitions;
- benchmark configuration;
- observed projection/serializer/fusion contracts.

A fingerprint mismatch is a comparison boundary, not something to silently
normalize away.

## Stability rule

Before the first promotion/tag, the candidate protocol may be corrected.

After `baseline-v1` is promoted:

- changing Ground Truth meaning;
- changing a metric formula;
- changing artifact semantics;
- or changing the comparison contract

requires explicit schema/protocol evolution. A semantic change must never be
silently published under the same frozen baseline identity.

## Measurement layers

Keep these separate:

1. **telemetry** — what the runtime did and how much it cost;
2. **diagnostics** — why candidates matched / how the backend translated work;
3. **quality metrics** — how ranked returned spans compare with reviewed GT.

FetchCore only returns evidence/candidates. Benchmark quality is not consumer
satisfaction and never grants authority.

## Running

The manifest's Ollama endpoint is used for both index preparation and dense
or hybrid query embeddings, independent of ambient Ollama environment variables.

The exact private checkout must already exist locally:

```bash
python benchmarks/baseline-v1/run.py \
  --workspace /path/to/NeuroTrack_MS \
  --arm opensearch-hybrid
```

To rebuild a remote projection before measuring:

```bash
python benchmarks/baseline-v1/run.py \
  --workspace /path/to/NeuroTrack_MS \
  --arm opensearch-hybrid \
  --prepare-index
```

To resume a partially completed version after interruption:

```bash
python benchmarks/baseline-v1/run.py \
  --workspace /path/to/the/same/NeuroTrack_MS/checkout \
  --resume V0.N
```

The runner checks completed artifacts against the selected arm, corpus, workspace,
environment, source revision, Ground Truth, metric and config fingerprints.
Index preparation telemetry is checkpointed before retrieval. Legacy partial
runs with lost indexing telemetry can be finished for retrieval analysis only
using `--allow-missing-indexing-telemetry`; they remain explicitly
`retrieval_complete`, not promotion-ready. Use
`--assume-no-index-preparation` only for a legacy partial run known to have
started without indexing.

Do not commit the local `.run-state.json` resume file or publish machine-specific
workspace paths. Preserve immutable measurement artifacts separately.

The runner validates the revision, clean worktree and Ground Truth coordinates
before probing services or starting measurement.

Every execution automatically receives the next monotonic run version under the
selected output root:

```text
artifacts/V0.1/
artifacts/V0.2/
artifacts/V0.3/
...
```

A successful execution writes `run.json` and a generated metrics `README.md` in
that directory. The report summarizes retrieval quality, latency and volume,
plus canonical and backend indexing metrics when `--prepare-index` is used. The
run version is provenance only and is intentionally excluded from the retrieval
configuration fingerprint so repeated measurements remain comparable. An interrupted
run can be resumed only in its existing version directory after validation.

The evidence-aware artifacts captured before automatic versioning are preserved
under `artifacts/V0.1/`. Their measurement payloads were moved without
modification; the reconstructed `run.json` records the migration provenance.
The first new execution using the versioned runner therefore starts at `V0.2`.

## Promotion gate

Baseline v1 is promoted only when all of the following are true:

1. corpus revision is immutable and the checkout is clean;
2. all 42 evidence requirements have reviewed valid coordinates;
3. Ground Truth schema/fingerprint and metrics schema/fingerprint are frozen;
4. all nine requested arms run their declared capability;
5. every returned top-K result has valid source line coordinates;
6. all arms meet the repetition protocol;
7. environment/config fingerprints are present, plus every projection,
   serializer, fusion or backend-specific fingerprint applicable to the
   capability actually executed;
8. failures are explicit rather than replaced by fallbacks;
9. the Solr performance anomaly is understood well enough to interpret its
   latency results honestly;
10. final evidence-aware artifacts have been reviewed before the tag.

The JSON contract remains independent of retrievalgate. External regression
testing may consume it without moving satisfaction/approval semantics into
FetchCore.
