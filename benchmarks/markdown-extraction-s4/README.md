# Markdown extraction S4 benchmark

This local benchmark holds a small redistributable Markdown corpus and six
versioned ground-truth targets fixed across the baseline and S4 arms.

## Run

From the repository root:

```bash
PYTHONPATH=src python benchmarks/markdown-extraction-s4/run.py
```

The JSON output includes canonical document/chunk counts, character volume,
exact ground-truth source-span recovery, a deterministic fixture-only lexical
ranking check, typed observed-link count, indexing p50/p95, and unchanged /
one-file-edit document diffs. `baseline.json` records the pre-S4 run from commit
`345d3e2b306aba579d73c062dacd59025c6ab461` using Python 3.12.3, one warmup, and
five measured runs. `ground-truth.json` and the fixture files define the shared
corpus and cases.

## Interpretation

The fixture ranker scores exact token overlap over canonical chunks only. Its
Recall@5, MRR, nDCG, and useful-evidence density are **not** measurements of a
production backend or general repository retrieval quality. Time-to-first-
candidate is unavailable because this extraction benchmark does not start a
retrieval backend.

The incremental section reports canonical document diffs after indexing the
same corpus and then editing one Markdown file. It does not imply incremental
scanning: active semantic indexers may require a full scan, and per-chunk
provenance includes the full source checksum.

This benchmark has no credential-bearing fixture. Unit tests verify that
query-bearing and user-info URLs are excluded from new typed link metadata, but
generic Markdown content remains subject to the existing scan policy and is not
universally sanitized.
