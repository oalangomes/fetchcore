# Baseline artifact layout

The evidence-aware retrieval artifacts currently stored directly in this
directory were captured on 2026-10-01 before automatic run versioning existed.
They are retained as historical evidence for PR #48 and are not silently
renamed or rewritten.

From the versioned runner onward, each benchmark execution is isolated under a
monotonic run directory:

```text
artifacts/
├── README.md
├── <historical pre-versioning artifacts>
├── V0.1/
│   ├── run.json
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

The root-level pre-versioning artifacts do not participate in the counter. The
first execution after this mechanism is introduced is `V0.1`.
