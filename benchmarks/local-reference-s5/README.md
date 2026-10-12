# Bounded local references S5 benchmark

Status: construction-only synthetic benchmark. Run from the repository root:

```bash
PYTHONPATH=src python benchmarks/local-reference-s5/run.py
```

The four checked-in source files and `ground-truth.json` are the frozen small
corpus. The runner copies them to a temporary workspace. Both arms use the
same safe API sanitizer and structured Python extractor; the only changed
variable is `resolve_local_references`. Seven measured passes follow one warmup.
A deterministic 100-file Markdown extension measures a larger full scan with
three passes. `results.json` records one local Linux/Python 3.11.16 run.

The fixture has three positive links and three hard negatives, including a
same-named route with the wrong method. The recorded run recovered 3/3 positive
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
