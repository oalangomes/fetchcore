# Baseline artifact layout

The evidence-aware retrieval artifacts captured on 2026-10-01 before automatic
run versioning existed are preserved as run `V0.1`. Their JSON measurement
payloads were moved without modification.

For current execution/publication instructions, see
[the BENCH runbook](../BENCH_RUNBOOK.md). Existing V0.1–V0.3 payloads are
frozen and **must not** be rewritten to impersonate dedicated BENCH runs.
V0.4 is added only from the completed operator run, in a separate commit.

From the versioned runner onward, each benchmark execution is isolated under a
monotonic run directory:

```text
artifacts/
├── README.md
├── V0.1/
│   ├── run.json
│   ├── README.md             # generated metrics report
│   ├── local-lexical.json
│   ├── solr-lexical.json
│   ├── solr-dense.json
│   ├── solr-hybrid.json
│   ├── opensearch-lexical.json
│   ├── opensearch-dense.json
│   ├── opensearch-hybrid.json
│   ├── qdrant-dense.json
│   ├── qdrant-hybrid.json
│   └── indexing.json        # only when --prepare-index is used
├── V0.2/
└── V0.3/
```

`fetchcore.baseline-v1` remains the protocol identity. `V0.N` identifies one
concrete execution and is provenance only; it does not alter retrieval config
fingerprints.

A complete versioned run has a `run.json` with `status: "complete"`. If an
execution fails after reserving a version, its directory may remain partial and
must not be treated as a completed baseline run. Resume the same version with
`--resume V0.N` after compatibility validation; completed arms are reused and
missing arms are measured. New runs receive monotonic versions.

Indexing telemetry is checkpointed immediately after preparation. A legacy
interrupted run with lost index telemetry may be restored only as
`status: "retrieval_complete"` (not promotion-ready) using the explicit
`--allow-missing-indexing-telemetry` option.

`.run-state.json` is local ephemeral state containing machine-specific details;
it must not be versioned or treated as a published result.

Every completed run has a generated `README.md` containing retrieval quality,
latency and volume tables. When indexing is prepared, the same report includes
canonical scan and per-backend indexing metrics.

`V0.1/run.json` was reconstructed during the layout migration. Its
`completed_at` value uses the timestamp of commit `5ca5278`, which incorporated
the frozen artifacts, because the original payloads did not record a wall-clock
completion time. The `provenance` field makes that distinction explicit.

Because `V0.1` is reserved by the migrated baseline, the next execution created
by the versioned runner is `V0.2`.
