# Baseline artifact layout

The evidence-aware retrieval artifacts captured on 2026-10-01 before automatic
run versioning existed are preserved as run `V0.1`. Their JSON measurement
payloads were moved without modification.

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
must not be treated as a completed baseline run. Versions are never reused.

Every completed run has a generated `README.md` containing retrieval quality,
latency and volume tables. When indexing is prepared, the same report includes
canonical scan and per-backend indexing metrics.

`V0.1/run.json` was reconstructed during the layout migration. Its
`completed_at` value uses the timestamp of commit `5ca5278`, which incorporated
the frozen artifacts, because the original payloads did not record a wall-clock
completion time. The `provenance` field makes that distinction explicit.

Because `V0.1` is reserved by the migrated baseline, the next execution created
by the versioned runner is `V0.2`.
