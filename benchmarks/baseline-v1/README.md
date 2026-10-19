# FetchCore baseline v1 candidate

This directory contains the executable candidate protocol for the first promoted
FetchCore retrieval/performance baseline.

## Corpus

- repository: `oalangomes/NeuroTrack_MS`
- revision: `74ce3244956a65baed61f1dae6f498cb2be4f3b7`
- corpus id: `neurotrack-ms-74ce3244`

The corpus is private. FetchCore stores no source content here.

## Standard operating procedure

Follow [BENCH_RUNBOOK.md](BENCH_RUNBOOK.md) for dedicated BENCH preflight,
new runs, resumable measurements, offline validation, ContextYield generation,
and immutable publication. New runs remain outside the versioned artifact tree.
The published catalog reserves version numbers even with a fresh local output
root. V0.1–V0.3 are already versioned legacy measurements and must not be
rewritten or retroactively attributed BENCH metadata. The operator will add
V0.4 measured artifacts in a separate commit to the standardization PR.

## New BENCH execution

For a new run, copy [run-config.example.json](run-config.example.json) outside
this directory, verify its endpoint/resource against `shared_infra.py env
--environment bench --repository oalangomes/NeuroTrack_MS`, set a real owner,
and run `run_bench.py` with `--run-config` and an output directory outside
`artifacts/`. The
mixed-case corpus repository identity remains the frozen Ground Truth identity;
only the resource name segment is lowercase, while its digest uses the exact identity.

`reuse_only` measures an already prepared snapshot. `--prepare-index` performs
a full rebuild and requires `provisioning: explicit_rebuild` in the new run
config. The wrapper writes BENCH owner, policy, resource and configuration
identity into `bench-run.json` beside the new run. `performance_comparable: true` requires
`dedicated: true` and `exclusive_host: true`, with a dedicated BENCH endpoint
and an actual exclusive host. The wrapper does not create a service, remove a
resource, or modify this frozen manifest, Ground Truth or historical results.
For a lexical-only run, the wrapper renders inapplicable embedding throughput as
`not measured` in the new report; measurements and frozen runner bytes are untouched.

`ground-truth.json` stores query intent plus reviewed evidence requirements as
repository paths and 1-based line regions. The selected revision intentionally
predates later harness-only changes.

## Candidate protocol

- Ground Truth schema: 2;
- artifact schema: 2;
- metrics schema: 1;
- 20 queries;
- 42 evidence requirements;
- requirements are AND; alternatives inside a requirement are OR;
- SourceRecall@20 is diagnostic;
- EvidenceRecall@20 and SpanCoverage@20 are primary coverage metrics;
- evidence MRR and evidence nDCG measure ranking;
- UsefulContextLineRatio measures known Ground Truth line density across the full returned set;
- 1 warm-up + 20 measured repetitions per query;
- max 20 returned results;
- diagnostic candidate window 80;
- Ollama + embeddinggemma + 768 dimensions for dense retrieval;
- RRF is the hybrid fusion baseline.

## Arms

1. local lexical
2. Solr lexical
3. Solr dense
4. Solr hybrid
5. OpenSearch lexical
6. OpenSearch dense
7. OpenSearch hybrid
8. Qdrant dense
9. Qdrant hybrid

No unsupported capability or failed backend may be silently replaced.

## Backend adapter boundary

The manifest is the runtime source of truth for arm ids, backend ids and
retrieval modes. The runner has no arm-name conditionals. Benchmark-specific
integration is isolated behind `BenchmarkBackendAdapter`, whose three
responsibilities are:

- build backend settings from the manifest context;
- probe service versions for environment provenance;
- prepare the backend index and return its telemetry, when applicable.

Adding a backend to a future benchmark matrix therefore requires implementing
the normal FetchCore backend and registering it in `BackendRegistry`, adding one
thin benchmark adapter to `benchmark_backend_adapters()`, and declaring its arms
and settings in the manifest. The runner's selection, version capture, index
dispatch, measurement and report generation do not need backend-specific
branches. A contract test exercises this extension path with a fixture backend.

The nine arms in `baseline-v1` remain frozen. A newly supported backend belongs
in a new protocol version or an explicitly experimental suite; the adapter
boundary makes that addition mechanical without rewriting this protocol.

## Fingerprint applicability

Promotion requires the environment and benchmark configuration identity for every
arm, plus the retrieval-contract fingerprints that are applicable to the path
actually executed.

This is intentionally capability-aware rather than a blanket requirement that
every arm expose every fingerprint:

- `local-lexical` uses the `local-semantic` safe-scan path directly and does
  **not** consume a rebuildable Projection. A projection fingerprint is therefore
  not expected for this arm;
- Solr, OpenSearch and Qdrant projection-backed arms must record the observed
  Projection fingerprint;
- dense and hybrid paths must record the embedding serializer fingerprint;
- hybrid paths must preserve their fusion input/output fingerprints;
- backend-specific retrieval contracts such as Qdrant sparse-analyzer identity
  must be recorded when applicable.

A missing fingerprint that should exist for the executed capability is a
comparison boundary and must not be silently accepted. The absence of a
non-applicable fingerprint is not an error.

## Artifact safety

Measured retrieval artifacts persist only ranked evaluation coordinates:

```text
rank
id
source_ref
line_start
line_end
```

That is enough to re-evaluate evidence metrics without publishing private source
content.

The runner validates Ground Truth coordinates against the exact local checkout
before measurement.

## Derived line-budget context yield

The frozen baseline-v1 artifact/metrics schemas remain unchanged. Budget-aware
evidence metrics are emitted as a separate derived artifact so the promoted ruler
is not silently redefined.

The derived evaluator uses ranked result spans already stored by baseline-v1 and
packs only complete returned spans in rank order. It stops before the first span
that would exceed the budget; results are never partially sliced to improve a
score.

The initial line budgets are:

```text
100
250
500
1000
```

For each budget it records:

- ContextYield — mean evidence-region span coverage inside the packed prefix;
- budgeted evidence recall — fully covered requirements inside the packed prefix;
- consumed lines;
- packed result count.

This measures **how much reviewed evidence arrives within bounded context**, which
is different from `UsefulContextLineRatio` (density across the full returned set).

Existing versioned runs can be re-evaluated offline without invoking retrieval:

```bash
python benchmarks/baseline-v1/context_yield.py \
  --artifacts benchmarks/baseline-v1/artifacts/V0.1
```

This writes `context-yield.json` and `context-yield.md` beside the frozen run.
The derived artifact fingerprints its metric definition and line-budget
configuration independently from baseline-v1 metrics schema v1.

## Run versioning

The protocol identity remains `fetchcore.baseline-v1`. Individual executions
receive a separate monotonic run version automatically.

The output root is scanned for directories matching `V0.<number>`; the runner
atomically reserves the next number before measurement:

```text
artifacts/
├── V0.1/
│   ├── run.json
│   ├── README.md
│   ├── local-lexical.json
│   ├── ...
│   └── indexing.json
├── V0.2/
│   ├── run.json
│   └── ...
└── V0.3/
    └── ...
```

A successful run writes `run.json` with:

- `run_version`;
- protocol;
- completion status/time;
- FetchCore revision;
- corpus identity;
- selected arms;
- whether indexing was prepared;
- the artifact filenames produced;
- the generated metrics report filename.

Each successful run also writes `README.md` beside `run.json`. It summarizes
retrieval quality, latency and volume for every selected arm. Runs created with
`--prepare-index` additionally include canonical and backend indexing metrics.
The report is derived from the JSON artifacts, which remain the source of truth.

The run version is provenance only. It is **not** added to the retrieval
configuration identity and therefore does not change `config_fingerprint`.
This is important: two otherwise identical executions remain directly
comparable even when they are `V0.1` and `V0.2`.

A run interrupted after reserving its directory may be resumed with
`--resume V0.N`, using the same frozen checkout and compatible environment.
The runner validates persisted arm artifacts before reuse and measures only
missing arms. Run state is checkpointed locally and index preparation telemetry
is written immediately after preparation, before retrieval starts. Completed
runs cannot be resumed.

Legacy interrupted runs may lack indexing telemetry from preparation. Resume
with `--allow-missing-indexing-telemetry` only to recover retrieval artifacts;
the resulting status is `retrieval_complete`, not a promotion-ready complete
baseline. Runs known not to have prepared an index must declare this with
`--assume-no-index-preparation`.

`.run-state.json` is machine-local resume state and must not be committed as a
benchmark artifact.

For a backend-free comparison of two complete frozen runs, use:

```bash
python benchmarks/baseline-v1/compare_runs.py \
  --baseline benchmarks/baseline-v1/artifacts/V0.1 \
  --candidate benchmarks/baseline-v1/artifacts/V0.2 \
  --output /tmp/baseline-v1-comparison.json
```

The comparator checks artifact identity and completeness, recomputes derived
ContextYield from frozen spans, and reports aggregate and per-query differences
across all measured repetitions. The [S0 verification
report](../../docs/benchmarks/baseline-v1-s0-verification.md) explains the V0.2
decision and its snapshot-provenance limit.

The evidence-aware artifacts captured before automatic versioning are preserved
as `V0.1`. Their JSON payloads were moved without modification, and the
reconstructed `run.json` records the migration provenance. Because `V0.1` is now
reserved, the first new execution with the versioned runner starts at `V0.2`.

## Indexing artifact

Within a versioned run, `artifacts/V0.N/indexing.json` is the consolidated
indexing record when `--prepare-index` is used for the complete nine-arm run.
The canonical corpus is materialized once and the required remote projections
are prepared once per backend.

It records independent Solr, OpenSearch and Qdrant snapshots and telemetry under
`backends`, including projection/serializer fingerprints, writes/deletes,
embedding work and duration.

Older per-arm indexing files were produced by earlier experimental executions.
They remain recoverable from Git history, but are not required for promotion
because the consolidated artifact preserves the indexing evidence for the final
run without duplicating equivalent rebuilds.

Indexing telemetry is evaluated separately from query latency.

## Current status

The source-level measurements captured on 2026-09-30 remain useful experimental
evidence, but they are **not the promoted baseline-v1**. They were produced
before evidence-region Ground Truth became the quality contract.

A complete evidence-aware run of all nine declared arms was captured on
2026-10-01 with artifact schema v2:

- 20 queries;
- 20 measured repetitions per query;
- 400 measured observations per arm;
- consistent Ground Truth and metrics fingerprints across all arms;
- ranked source spans persisted for offline re-evaluation.

The earlier multi-second Solr retrieval anomaly was traced to per-request
connection setup and removed by the persistent Solr transport. In the current
run, Solr query latency is in the same operational range as the other remote
search backends:

| Arm | p50 | p95 | p99 |
| --- | ---: | ---: | ---: |
| Solr lexical | 17 ms | 28 ms | 33 ms |
| Solr dense | 33 ms | 46 ms | 54 ms |
| Solr hybrid | 52 ms | 70 ms | 78 ms |

Solr full-rebuild indexing remains materially slower than OpenSearch and Qdrant
in this environment. That is retained as separate indexing telemetry and does
not change retrieval quality measurements.

Baseline v1 is therefore in **final review before promotion**, not waiting for a
new evidence-aware execution. Promotion still requires the final artifact review
and tag/freeze decision.

See `docs/benchmarks/baseline-v1.md` for metric definitions and the promotion
gate.
