# Markdown extraction S4 benchmark

This local benchmark holds a small redistributable Markdown corpus and six
versioned ground-truth targets fixed across the generic baseline, pre-correction
S4, and corrected S4.1 arms.

## Run

From the repository root:

```bash
PYTHONPATH=src python benchmarks/markdown-extraction-s4/run.py
```

The JSON output includes canonical document/chunk counts, source/content bytes,
exact ground-truth source-span recovery, fixture-only lexical ranking,
observation-provenance violations, evidence and canonical-payload sizes,
indexing p50/p95, stress cases, and unchanged / one-file-edit diffs.
`baseline.json` records the pre-S4 run from commit
`345d3e2b306aba579d73c062dacd59025c6ab461`. `pre-correction-results.json`
preserves the S4 result published before the P1 fixes, from commit
`81aaf2878d29256beb59c527f3b892e3697786ab`. `results.json` records corrected
S4.1. All used Linux, Python 3.12.3, one warmup, and five measured runs.
`ground-truth.json` and the fixture files define the shared corpus and cases.

## Recorded comparison

| Metric | Generic baseline | S4 as previously recorded | Corrected S4.1 (paired run) |
| --- | ---: | ---: | ---: |
| Ground-truth facts recovered | 6/6 | 6/6 | 6/6 |
| Exact expected source spans | 4/6 | 6/6 | 6/6 |
| Fixture-ranker Recall@5 / MRR / nDCG@5 | 1.0 / 1.0 / 1.0 | 1.0 / 1.0 / 1.0 | 1.0 / 1.0 / 1.0 |
| Useful evidence density@3 | 0.4040 | 0.4653 | 0.4653 |
| Canonical documents / content chars | 17 / 1,789 | 17 / 1,789 | 17 / 1,789 |
| Maximum chunk characters | 312 | 252 | 252 |
| Safe typed link observations | 0 | 3 | 3 |
| Index p50 / p95 (ms) | 7.216 / 8.079 | 7.001 / 7.966 | 8.879 / 9.651 |
| Unchanged re-index adds / updates / removals | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| One-file edit updated documents | 2 | 2 | 2 |
| Corrected one-file edit unchanged documents | n/a | n/a | 15 |

The table uses the frozen baseline, the result recorded in the original PR, and
a paired same-host run of the pre-correction commit against corrected code. The
paired p50/p95 changed **6.804/6.966 ms → 8.879/9.651 ms**; the one-file edit
was 7.696 → 7.999 ms. This is a measured 2.1 ms p50 / 2.7 ms p95 increase on
the tiny fixture, primarily due to extracting and validating observations per
emitted chunk instead of once per section. Five-sample timings vary on this
shared host; do not extrapolate the relative percentage to larger repositories.
Correctness takes precedence here, and the trade-off is reported rather than
hidden.

The provenance gates report zero references outside their document spans,
zero references absent from retained content, zero unjustified duplicates,
zero code languages absent from retained content, and zero observations
propagated from truncated regions. On stress fixtures, a 100,000-character
heading with a 100-character chunk budget yields a 2,421-byte canonical
Projection v3 payload (the pre-fix reproduction was 202,234 bytes); 1,000 links
are capped at 16 observed references and 1,472 evidence bytes. The corpus has
38,930 canonical document/payload bytes, a 2,740-byte maximum
`canonical_payload`, and a 537-byte maximum section-evidence payload; the
documented hard section-evidence limit is 8,192 bytes.

The clear measured improvement is exact section-span recovery: the Setext
architecture section and code inside the duplicate-heading branch now have
precise locators. The fixture ranking metrics do not change and are already
saturated; they are not evidence of production ranking quality. Timings are
small local synthetic measurements with five samples. The corrected path has a
measured local indexing cost versus pre-correction S4; it is not a general
performance estimate. Time-to-first-candidate is unavailable because the
benchmark starts no retrieval backend.

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
