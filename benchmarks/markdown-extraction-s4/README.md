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
five measured runs. `results.json` records S4 from commit
`81aaf2878d29256beb59c527f3b892e3697786ab` under the same environment and
configuration. `ground-truth.json` and the fixture files define the shared
corpus and cases.

## Recorded comparison

| Metric | Baseline | S4 |
| --- | ---: | ---: |
| Ground-truth facts recovered | 6/6 | 6/6 |
| Exact expected source spans | 4/6 | 6/6 |
| Fixture-ranker Recall@5 / MRR / nDCG@5 | 1.0 / 1.0 / 1.0 | 1.0 / 1.0 / 1.0 |
| Useful evidence density@3 | 0.4040 | 0.4653 |
| Canonical documents / content characters | 17 / 1,789 | 17 / 1,789 |
| Maximum chunk characters | 312 | 252 |
| Safe typed link observations | 0 | 3 |
| Index p50 / p95 (ms) | 7.216 / 8.079 | 7.001 / 7.966 |
| Unchanged re-index additions / updates / removals | 0 / 0 / 0 | 0 / 0 / 0 |
| One-file edit updated documents | 2 | 2 |

The clear measured improvement is exact section-span recovery: the Setext
architecture section and code inside the duplicate-heading branch now have
precise locators. The fixture ranking metrics do not change and are already
saturated; they are not evidence of production ranking quality. Timings are
small local synthetic measurements with five samples and should not be
interpreted as a general performance improvement or regression. The one-file
edit timing is a single observation, not p50/p95.

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
