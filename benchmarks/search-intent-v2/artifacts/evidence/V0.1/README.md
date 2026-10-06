# SearchIntent v2 evidence evaluation

Protocol: `fetchcore.search-intent-v2-evidence`. SMOKE / partial protocol.
Delta = canonical - legacy. Quality is evidence recovery, not consumer satisfaction.
Latency is backend-reported end-to-end milliseconds; indexing and serving smoke excluded.

| Arm | Representation | SourceRecall | EvidenceRecall | SpanCoverage | EvidenceMRR | EvidenceNDCG | UsefulContext | Candidates | ZeroResult | p50 | p95 | p99 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| [local-lexical](local-lexical-legacy.json.gz) | legacy | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 1.000000 | 3883.000000 | 4486.000000 | 4718.000000 |
| [local-lexical](local-lexical-canonical.json.gz) | canonical | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 1.000000 | 3955.000000 | 4306.000000 | 4539.000000 |

## Operational volume

| Arm | Representation | Returned candidates | Returned lines | Returned chars | Observations |
|---|---|---:|---:|---:|---:|
| local-lexical | legacy | 0.000 | 0.000 | 0.000 | 20.000 |
| local-lexical | canonical | 0.000 | 0.000 | 0.000 | 20.000 |

## Paired changes

| Arm | Δ EvidenceRecall | Δ SpanCoverage | Δ EvidenceMRR | Δ UsefulContext | Candidates before → after (Δ) | p50 before → after (Δ) |
|---|---:|---:|---:|---:|---|---|
| local-lexical | +0.000000 | +0.000000 | +0.000000 | +0.000000 | 0.00 → 0.00 (+0.00) | 3883 → 3955 (+72) |

## Context within line budgets

| Arm | Representation | Budget | ContextYield | EvidenceRecall | Consumed lines | Packed results |
|---|---|---:|---:|---:|---:|---:|
| local-lexical | legacy | 100 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| local-lexical | legacy | 250 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| local-lexical | legacy | 500 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| local-lexical | legacy | 1000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| local-lexical | canonical | 100 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| local-lexical | canonical | 250 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| local-lexical | canonical | 500 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| local-lexical | canonical | 1000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |

## Query regressions

Classification: any decrease in EvidenceRecall, SpanCoverage or EvidenceMRR is regressed;
otherwise any increase is improved; otherwise unchanged. Mixed gains/losses remain regressed.

- **local-lexical**: {'improved': 0, 'unchanged': 20, 'regressed': 0}

All query deltas and absolute operational costs: [comparison.json](comparison.json).
Translation diagnostics, runtime telemetry and shared evaluator observations are separate artifact fields.
No time-to-first-candidate metric is available from the current runtime.
