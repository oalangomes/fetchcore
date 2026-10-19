> **Classification:** synthetic acceptance fixture, not a production benchmark. The historical timing/metric records below remain archival observations.

# Bounded local references S5 benchmark

Status: construction-only synthetic benchmark. Run from the repository root:

```bash
PYTHONPATH=src python tests/acceptance/acquisition/local-relations/run.py
```

The original S5 baseline used four source files; `results.json` preserves its
measurements. The corrective fixture has five source files and extends the
original ground truth. The runner copies them to a temporary workspace. Both arms use the
same safe API sanitizer and structured Python extractor; the within-revision
baseline and S5 arms differ only in `resolve_local_references`. Eleven measured
passes follow one warmup. A deterministic 100-file Markdown extension measures
a larger full scan with nine passes. `results.json` records the original local
Linux/Python 3.11.16 run under the former seven/three-pass protocol.

The original fixture had three positive links and three hard negatives,
including a same-named route with the wrong method. That recorded run recovered 3/3 positive
targets, with precision 1.0 and false-positive link rate 0/3. Six observations
yielded three resolved, two unresolved and one blocked result. No ranked
retrieval backend runs, so these figures are **link-construction metrics, not
Recall@K**. The three candidates examined are exact-index bucket entries.

Small-corpus indexing p50/p95 was 8.320/8.390 ms without S5 and 8.515/9.466
ms with S5. Isolated resolution p50/p95 was 0.511/0.550 ms. On 103 files,
indexing p50 was 44.100 ms without S5 and 52.827 ms with S5. Canonical payload
grew 4,124 bytes on the small corpus, with 0.1218 resolved links per 1,000
canonical characters. Removing `architecture.md` removed its link and updated
one canonical document. Shared-host timings vary; these small samples are not
a workload-wide latency estimate.

The active interpreters and resolver currently require full scans for changed
paths. The benchmark measures that cost; it does not claim incremental
resolution or re-embedding savings. `retrievalgate` is unnecessary for this
construction-only experiment because no ranked retrieval claim is made.

## S5 correction on PR #173

`results.json` preserves the original S5 baseline. The corrective corpus adds
`unrelated.py`, whose
`@app.get("/shadow/{id}")` has the same method and path as a new OpenAPI
operation but whose `app` is `Unrelated()`. The unused FastAPI import is
intentional. `corrective-before.json` uses source revision `d5049d8`;
`corrective-after.json` uses the correction. `corrective-comparison.json`
records five additional, alternated before/after pairs. Every run used the
same five-file corpus and 100 generated Markdown files, Python 3.11.16,
11 small-corpus passes, nine large-corpus passes, and nearest-rank p50/p95.
Only implementation source changed between arms. A separate budget-pressure
run sets `max_references=1` on the same small corpus. Reproduce with
`python tests/acceptance/acquisition/local-relations/compare.py --before-src PATH_TO_D5049D8/src`.
Timings below are medians of the five recorded run summaries; the JSON keeps
every sample and its range.

| Metric | Before | After |
| --- | ---: | ---: |
| Ground truth positives recovered | 3/3 | 3/3 |
| False-positive contract/code links | 1 | 0 |
| Positive relations emitted | 4 | 3 |
| Inconclusive joins lacking receiver proof | 0 | 1 |
| Examined declarations, default budget | 7 | 7 |
| Omitted declarations, budget 1 | unavailable | 6 |
| Resolution complete, default / pressure | unavailable | yes / no |
| Budget exhaustion rate across two passes | unavailable | 1/2 |
| Exact candidates examined, default budget | 4 | 4 |
| Additional canonical bytes | 5,073 | 4,717 |
| Resolution p50 / p95, ms | 0.736 / 0.982 | 1.180 / 1.847 |
| Small-corpus S5 index p50 / p95, ms | 14.065 / 16.510 | 13.923 / 17.446 |
| 104-file S5 index p50 / p95, ms | 83.667 / 126.994 | 83.874 / 107.704 |

The correction removes the demonstrated false link and keeps the three
original positives. Median isolated resolution p50 rose 0.444 ms (60.3%),
although small-corpus S5 indexing p50 fell 0.142 ms (1.0%) and 104-file
indexing p50 rose 0.207 ms (0.2%). Resolution p95 also rose in this sample.
The five run summaries had wide ranges, particularly at p95, so the measured
indexing differences are inconclusive. The run does not isolate binding proof
from coverage diagnostics. No ranked retrieval ran, so these are construction
metrics, not production Recall@K or MRR. Canonical diff after target removal
still drops the old relation and updates one document.

## Final corrective review on PR #173

The existing `compare.py` runner was reused for three alternated pairs against
the reviewed `87025a7` tree and the final corrective tree. The five-file
corrective fixture, 100 generated Markdown files, ground truth and protocol
were unchanged. This run was on the same Python 3.11.16 environment; the
earlier artifacts above remain intact.

| Metric | `87025a7` | Final correction |
| --- | ---: | ---: |
| Ground truth positives recovered | 3/3 | 3/3 |
| False contract/code links | 0 | 0 |
| Positive relations emitted | 3 | 3 |
| Additional canonical bytes | 4,717 | 4,717 |
| Isolated resolution p50 / p95, ms | 1.101 / 1.332 | 1.116 / 1.692 |
| Small S5 index p50 / p95, ms | 14.069 / 20.090 | 14.016 / 17.437 |
| 104-file S5 index p50 / p95, ms | 84.289 / 99.803 | 89.953 / 115.302 |

The three-pair median of the large S5 index p50 rose 5.664 ms (6.7%). The
final serialized-evidence budget check adds another corpus evidence pass,
while oversized fragment buckets now stop before scanning their chunks.
The large-arm p95 varied widely across pairs, so these timings do not establish
a stable latency change. The original positives and canonical bytes were
unchanged on the frozen fixture. Cross-revision identity collisions, Python
pattern captures and final-byte-budget failures are covered by regressions,
not by this construction fixture.
