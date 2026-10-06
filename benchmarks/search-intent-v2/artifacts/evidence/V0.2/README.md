# SearchIntent v2 evidence evaluation

Protocol: `fetchcore.search-intent-v2-evidence`. Full measurement protocol.
Delta = canonical - legacy. Quality is evidence recovery, not consumer satisfaction.
Latency is backend-reported end-to-end milliseconds; indexing and serving smoke excluded.

| Arm | Representation | SourceRecall | EvidenceRecall | SpanCoverage | EvidenceMRR | EvidenceNDCG | UsefulContext | Candidates | ZeroResult | p50 | p95 | p99 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| [local-lexical](local-lexical-legacy.json.gz) | legacy | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 1.000000 | 3440.000000 | 4025.000000 | 4247.000000 |
| [local-lexical](local-lexical-canonical.json.gz) | canonical | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 1.000000 | 3424.000000 | 3998.000000 | 4275.000000 |
| [solr-lexical](solr-lexical-legacy.json.gz) | legacy | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 1.000000 | 2.000000 | 3.000000 | 4.000000 |
| [solr-lexical](solr-lexical-canonical.json.gz) | canonical | 0.650000 | 0.291667 | 0.310652 | 0.232292 | 0.266555 | 0.011000 | 80.000000 | 0.000000 | 19.000000 | 28.000000 | 39.000000 |
| [solr-dense](solr-dense-legacy.json.gz) | legacy | 0.900000 | 0.800000 | 0.831587 | 0.685000 | 0.696128 | 0.063281 | 80.000000 | 0.000000 | 30.000000 | 44.000000 | 60.000000 |
| [solr-dense](solr-dense-canonical.json.gz) | canonical | 0.900000 | 0.816667 | 0.848254 | 0.734402 | 0.747637 | 0.056165 | 80.000000 | 0.000000 | 30.000000 | 41.000000 | 60.000000 |
| [solr-hybrid](solr-hybrid-legacy.json.gz) | legacy | 0.900000 | 0.800000 | 0.831587 | 0.685000 | 0.696128 | 0.063281 | 20.000000 | 0.000000 | 34.000000 | 50.000000 | 82.000000 |
| [solr-hybrid](solr-hybrid-canonical.json.gz) | canonical | 0.850000 | 0.800000 | 0.822064 | 0.539167 | 0.603088 | 0.038965 | 37.150000 | 0.000000 | 52.000000 | 85.000000 | 111.000000 |
| [opensearch-lexical](opensearch-lexical-legacy.json.gz) | legacy | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 1.000000 | 4.000000 | 8.000000 | 28.000000 |
| [opensearch-lexical](opensearch-lexical-canonical.json.gz) | canonical | 0.700000 | 0.316667 | 0.335652 | 0.237500 | 0.266963 | 0.011323 | 80.000000 | 0.000000 | 28.000000 | 54.000000 | 76.000000 |
| [opensearch-dense](opensearch-dense-legacy.json.gz) | legacy | 0.900000 | 0.800000 | 0.831587 | 0.676667 | 0.689581 | 0.060255 | 80.000000 | 0.000000 | 38.000000 | 64.000000 | 105.000000 |
| [opensearch-dense](opensearch-dense-canonical.json.gz) | canonical | 0.950000 | 0.841667 | 0.873254 | 0.737527 | 0.759869 | 0.054106 | 80.000000 | 0.000000 | 38.000000 | 75.000000 | 104.000000 |
| [opensearch-hybrid](opensearch-hybrid-legacy.json.gz) | legacy | 0.900000 | 0.800000 | 0.831587 | 0.676667 | 0.689581 | 0.060255 | 20.000000 | 0.000000 | 42.000000 | 93.000000 | 126.000000 |
| [opensearch-hybrid](opensearch-hybrid-canonical.json.gz) | canonical | 0.850000 | 0.800000 | 0.822064 | 0.533333 | 0.588570 | 0.038790 | 36.750000 | 0.000000 | 65.000000 | 114.000000 | 142.000000 |
| [qdrant-dense](qdrant-dense-legacy.json.gz) | legacy | 0.900000 | 0.800000 | 0.831587 | 0.676667 | 0.688880 | 0.057154 | 80.000000 | 0.000000 | 1039.000000 | 1123.000000 | 1199.000000 |
| [qdrant-dense](qdrant-dense-canonical.json.gz) | canonical | 0.950000 | 0.841667 | 0.873254 | 0.728700 | 0.751316 | 0.053948 | 80.000000 | 0.000000 | 1034.000000 | 1112.000000 | 1143.000000 |
| [qdrant-hybrid](qdrant-hybrid-legacy.json.gz) | legacy | 0.950000 | 0.900000 | 0.916111 | 0.723809 | 0.744390 | 0.031324 | 145.800000 | 0.000000 | 1427.000000 | 1590.000000 | 1653.000000 |
| [qdrant-hybrid](qdrant-hybrid-canonical.json.gz) | canonical | 0.950000 | 0.900000 | 0.908889 | 0.744643 | 0.762584 | 0.028770 | 144.800000 | 0.000000 | 1419.000000 | 1577.000000 | 1775.000000 |

## Operational volume

| Arm | Representation | Returned candidates | Returned lines | Returned chars | Observations |
|---|---|---:|---:|---:|---:|
| local-lexical | legacy | 0.000 | 0.000 | 0.000 | 400.000 |
| local-lexical | canonical | 0.000 | 0.000 | 0.000 | 400.000 |
| solr-lexical | legacy | 0.000 | 0.000 | 0.000 | 400.000 |
| solr-lexical | canonical | 20.000 | 688.100 | 28758.600 | 400.000 |
| solr-dense | legacy | 20.000 | 648.600 | 22201.000 | 400.000 |
| solr-dense | canonical | 20.000 | 684.250 | 23467.950 | 400.000 |
| solr-hybrid | legacy | 20.000 | 648.600 | 22201.000 | 400.000 |
| solr-hybrid | canonical | 20.000 | 753.850 | 29174.650 | 400.000 |
| opensearch-lexical | legacy | 0.000 | 0.000 | 0.000 | 400.000 |
| opensearch-lexical | canonical | 20.000 | 726.650 | 29936.500 | 400.000 |
| opensearch-dense | legacy | 20.000 | 666.000 | 22548.700 | 400.000 |
| opensearch-dense | canonical | 20.000 | 709.850 | 24495.500 | 400.000 |
| opensearch-hybrid | legacy | 20.000 | 666.000 | 22548.700 | 400.000 |
| opensearch-hybrid | canonical | 20.000 | 762.100 | 30214.550 | 400.000 |
| qdrant-dense | legacy | 20.000 | 673.300 | 22796.300 | 400.000 |
| qdrant-dense | canonical | 20.000 | 720.950 | 24801.650 | 400.000 |
| qdrant-hybrid | legacy | 20.000 | 1255.250 | 241589.850 | 400.000 |
| qdrant-hybrid | canonical | 20.000 | 1277.700 | 217821.000 | 400.000 |

## Paired changes

| Arm | Δ EvidenceRecall | Δ SpanCoverage | Δ EvidenceMRR | Δ UsefulContext | Candidates before → after (Δ) | p50 before → after (Δ) |
|---|---:|---:|---:|---:|---|---|
| local-lexical | +0.000000 | +0.000000 | +0.000000 | +0.000000 | 0.00 → 0.00 (+0.00) | 3440 → 3424 (-16) |
| solr-lexical | +0.291667 | +0.310652 | +0.232292 | +0.011000 | 0.00 → 80.00 (+80.00) | 2 → 19 (+17) |
| solr-dense | +0.016667 | +0.016667 | +0.049402 | -0.007116 | 80.00 → 80.00 (+0.00) | 30 → 30 (+0) |
| solr-hybrid | +0.000000 | -0.009523 | -0.145833 | -0.024316 | 20.00 → 37.15 (+17.15) | 34 → 52 (+18) |
| opensearch-lexical | +0.316667 | +0.335652 | +0.237500 | +0.011323 | 0.00 → 80.00 (+80.00) | 4 → 28 (+24) |
| opensearch-dense | +0.041667 | +0.041667 | +0.060860 | -0.006149 | 80.00 → 80.00 (+0.00) | 38 → 38 (+0) |
| opensearch-hybrid | +0.000000 | -0.009523 | -0.143334 | -0.021465 | 20.00 → 36.75 (+16.75) | 42 → 65 (+23) |
| qdrant-dense | +0.041667 | +0.041667 | +0.052033 | -0.003206 | 80.00 → 80.00 (+0.00) | 1039 → 1034 (-5) |
| qdrant-hybrid | +0.000000 | -0.007222 | +0.020834 | -0.002554 | 145.80 → 144.80 (-1.00) | 1427 → 1419 (-8) |

## Context within line budgets

| Arm | Representation | Budget | ContextYield | EvidenceRecall | Consumed lines | Packed results |
|---|---|---:|---:|---:|---:|---:|
| local-lexical | legacy | 100 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| local-lexical | legacy | 1000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| local-lexical | legacy | 250 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| local-lexical | legacy | 500 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| local-lexical | canonical | 100 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| local-lexical | canonical | 1000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| local-lexical | canonical | 250 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| local-lexical | canonical | 500 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| solr-lexical | legacy | 100 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| solr-lexical | legacy | 1000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| solr-lexical | legacy | 250 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| solr-lexical | legacy | 500 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| solr-lexical | canonical | 100 | 0.068985 | 0.050000 | 51.850000 | 3.400000 |
| solr-lexical | canonical | 1000 | 0.293985 | 0.275000 | 587.800000 | 18.650000 |
| solr-lexical | canonical | 250 | 0.293985 | 0.275000 | 205.450000 | 8.850000 |
| solr-lexical | canonical | 500 | 0.293985 | 0.275000 | 392.700000 | 14.200000 |
| solr-dense | legacy | 100 | 0.532778 | 0.483333 | 59.500000 | 3.750000 |
| solr-dense | legacy | 1000 | 0.831587 | 0.800000 | 567.000000 | 18.900000 |
| solr-dense | legacy | 250 | 0.806587 | 0.775000 | 192.700000 | 10.350000 |
| solr-dense | legacy | 500 | 0.819087 | 0.787500 | 373.800000 | 14.900000 |
| solr-dense | canonical | 100 | 0.548254 | 0.508333 | 54.350000 | 3.650000 |
| solr-dense | canonical | 1000 | 0.848254 | 0.816667 | 615.900000 | 18.650000 |
| solr-dense | canonical | 250 | 0.799444 | 0.750000 | 194.050000 | 8.900000 |
| solr-dense | canonical | 500 | 0.835754 | 0.804167 | 404.600000 | 14.050000 |
| solr-hybrid | legacy | 100 | 0.532778 | 0.483333 | 59.500000 | 3.750000 |
| solr-hybrid | legacy | 1000 | 0.831587 | 0.800000 | 567.000000 | 18.900000 |
| solr-hybrid | legacy | 250 | 0.806587 | 0.775000 | 192.700000 | 10.350000 |
| solr-hybrid | legacy | 500 | 0.819087 | 0.787500 | 373.800000 | 14.900000 |
| solr-hybrid | canonical | 100 | 0.455397 | 0.433333 | 54.050000 | 3.550000 |
| solr-hybrid | canonical | 1000 | 0.805397 | 0.783333 | 677.300000 | 18.900000 |
| solr-hybrid | canonical | 250 | 0.755397 | 0.733333 | 202.650000 | 7.700000 |
| solr-hybrid | canonical | 500 | 0.792897 | 0.770833 | 409.800000 | 13.200000 |
| opensearch-lexical | legacy | 100 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| opensearch-lexical | legacy | 1000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| opensearch-lexical | legacy | 250 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| opensearch-lexical | legacy | 500 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| opensearch-lexical | canonical | 100 | 0.093985 | 0.075000 | 57.600000 | 3.500000 |
| opensearch-lexical | canonical | 1000 | 0.335652 | 0.316667 | 611.000000 | 18.400000 |
| opensearch-lexical | canonical | 250 | 0.243985 | 0.225000 | 201.100000 | 8.650000 |
| opensearch-lexical | canonical | 500 | 0.243985 | 0.225000 | 390.600000 | 13.500000 |
| opensearch-dense | legacy | 100 | 0.532778 | 0.483333 | 59.500000 | 3.750000 |
| opensearch-dense | legacy | 1000 | 0.831587 | 0.800000 | 587.450000 | 18.900000 |
| opensearch-dense | legacy | 250 | 0.756587 | 0.725000 | 184.650000 | 9.550000 |
| opensearch-dense | legacy | 500 | 0.819087 | 0.787500 | 377.800000 | 14.300000 |
| opensearch-dense | canonical | 100 | 0.548254 | 0.508333 | 55.100000 | 3.700000 |
| opensearch-dense | canonical | 1000 | 0.873254 | 0.841667 | 637.400000 | 18.650000 |
| opensearch-dense | canonical | 250 | 0.799444 | 0.750000 | 194.950000 | 8.700000 |
| opensearch-dense | canonical | 500 | 0.860754 | 0.829167 | 403.000000 | 13.700000 |
| opensearch-hybrid | legacy | 100 | 0.532778 | 0.483333 | 59.500000 | 3.750000 |
| opensearch-hybrid | legacy | 1000 | 0.831587 | 0.800000 | 587.450000 | 18.900000 |
| opensearch-hybrid | legacy | 250 | 0.756587 | 0.725000 | 184.650000 | 9.550000 |
| opensearch-hybrid | legacy | 500 | 0.819087 | 0.787500 | 377.800000 | 14.300000 |
| opensearch-hybrid | canonical | 100 | 0.331587 | 0.308333 | 48.450000 | 3.450000 |
| opensearch-hybrid | canonical | 1000 | 0.822064 | 0.800000 | 692.400000 | 19.200000 |
| opensearch-hybrid | canonical | 250 | 0.780397 | 0.758333 | 200.800000 | 7.400000 |
| opensearch-hybrid | canonical | 500 | 0.792897 | 0.770833 | 419.550000 | 12.800000 |
| qdrant-dense | legacy | 100 | 0.532778 | 0.483333 | 61.500000 | 3.900000 |
| qdrant-dense | legacy | 250 | 0.756587 | 0.725000 | 187.700000 | 9.600000 |
| qdrant-dense | legacy | 500 | 0.819087 | 0.787500 | 380.550000 | 14.250000 |
| qdrant-dense | legacy | 1000 | 0.831587 | 0.800000 | 591.700000 | 18.900000 |
| qdrant-dense | canonical | 100 | 0.548254 | 0.508333 | 57.400000 | 3.800000 |
| qdrant-dense | canonical | 250 | 0.749444 | 0.700000 | 193.100000 | 8.550000 |
| qdrant-dense | canonical | 500 | 0.860754 | 0.829167 | 405.650000 | 13.350000 |
| qdrant-dense | canonical | 1000 | 0.873254 | 0.841667 | 644.400000 | 18.550000 |
| qdrant-hybrid | legacy | 100 | 0.398237 | 0.358333 | 40.300000 | 1.700000 |
| qdrant-hybrid | legacy | 250 | 0.658889 | 0.650000 | 184.900000 | 4.000000 |
| qdrant-hybrid | legacy | 500 | 0.774444 | 0.758333 | 433.750000 | 8.250000 |
| qdrant-hybrid | legacy | 1000 | 0.903611 | 0.887500 | 882.100000 | 15.550000 |
| qdrant-hybrid | canonical | 100 | 0.410079 | 0.383333 | 41.750000 | 1.750000 |
| qdrant-hybrid | canonical | 250 | 0.658889 | 0.650000 | 177.550000 | 3.500000 |
| qdrant-hybrid | canonical | 500 | 0.792222 | 0.783333 | 441.850000 | 7.800000 |
| qdrant-hybrid | canonical | 1000 | 0.896389 | 0.887500 | 897.000000 | 15.050000 |

## Query regressions

Classification: any decrease in EvidenceRecall, SpanCoverage or EvidenceMRR is regressed;
otherwise any increase is improved; otherwise unchanged. Mixed gains/losses remain regressed.

- **local-lexical**: {'improved': 0, 'unchanged': 20, 'regressed': 0}
- **solr-lexical**: {'improved': 8, 'unchanged': 12, 'regressed': 0}
- **solr-dense**: {'improved': 3, 'unchanged': 16, 'regressed': 1}
  - `architecture-overview`: Δ evidence_recall_at_k=+0.000000, Δ span_coverage_at_k=+0.000000, Δ evidence_reciprocal_rank=-0.088889
- **solr-hybrid**: {'improved': 4, 'unchanged': 7, 'regressed': 9}
  - `agent-development-rules`: Δ evidence_recall_at_k=+0.000000, Δ span_coverage_at_k=+0.000000, Δ evidence_reciprocal_rank=-0.500000
  - `ai-queue-subject-context`: Δ evidence_recall_at_k=+0.000000, Δ span_coverage_at_k=-0.190476, Δ evidence_reciprocal_rank=-0.750000
  - `ai-worker-processing`: Δ evidence_recall_at_k=+0.000000, Δ span_coverage_at_k=+0.000000, Δ evidence_reciprocal_rank=-0.166667
  - `home-active-subject-data`: Δ evidence_recall_at_k=+0.000000, Δ span_coverage_at_k=+0.000000, Δ evidence_reciprocal_rank=-0.666667
  - `home-prompt-controller`: Δ evidence_recall_at_k=+0.000000, Δ span_coverage_at_k=+0.000000, Δ evidence_reciprocal_rank=-0.500000
  - `medication-create-controller-validation`: Δ evidence_recall_at_k=+0.000000, Δ span_coverage_at_k=+0.000000, Δ evidence_reciprocal_rank=-0.500000
  - `medication-update-owner-service`: Δ evidence_recall_at_k=+0.000000, Δ span_coverage_at_k=+0.000000, Δ evidence_reciprocal_rank=-0.166667
  - `mfa-controller-errors`: Δ evidence_recall_at_k=+0.000000, Δ span_coverage_at_k=+0.000000, Δ evidence_reciprocal_rank=-0.800000
  - `mfa-totp-service`: Δ evidence_recall_at_k=+0.000000, Δ span_coverage_at_k=+0.000000, Δ evidence_reciprocal_rank=-0.500000
- **opensearch-lexical**: {'improved': 8, 'unchanged': 12, 'regressed': 0}
- **opensearch-dense**: {'improved': 5, 'unchanged': 14, 'regressed': 1}
  - `architecture-overview`: Δ evidence_recall_at_k=+0.000000, Δ span_coverage_at_k=+0.000000, Δ evidence_reciprocal_rank=-0.088889
- **opensearch-hybrid**: {'improved': 4, 'unchanged': 7, 'regressed': 9}
  - `agent-development-rules`: Δ evidence_recall_at_k=+0.000000, Δ span_coverage_at_k=+0.000000, Δ evidence_reciprocal_rank=-0.500000
  - `ai-queue-subject-context`: Δ evidence_recall_at_k=+0.000000, Δ span_coverage_at_k=-0.190476, Δ evidence_reciprocal_rank=-0.750000
  - `ai-worker-processing`: Δ evidence_recall_at_k=+0.000000, Δ span_coverage_at_k=+0.000000, Δ evidence_reciprocal_rank=-0.250000
  - `health-resource-collection`: Δ evidence_recall_at_k=+0.000000, Δ span_coverage_at_k=+0.000000, Δ evidence_reciprocal_rank=-0.500000
  - `home-active-subject-data`: Δ evidence_recall_at_k=+0.000000, Δ span_coverage_at_k=+0.000000, Δ evidence_reciprocal_rank=-0.666667
  - `home-prompt-controller`: Δ evidence_recall_at_k=+0.000000, Δ span_coverage_at_k=+0.000000, Δ evidence_reciprocal_rank=-0.500000
  - `medication-create-controller-validation`: Δ evidence_recall_at_k=+0.000000, Δ span_coverage_at_k=+0.000000, Δ evidence_reciprocal_rank=-0.500000
  - `medication-update-owner-service`: Δ evidence_recall_at_k=+0.000000, Δ span_coverage_at_k=+0.000000, Δ evidence_reciprocal_rank=-0.250000
  - `mfa-controller-errors`: Δ evidence_recall_at_k=+0.000000, Δ span_coverage_at_k=+0.000000, Δ evidence_reciprocal_rank=-0.750000
- **qdrant-dense**: {'improved': 4, 'unchanged': 15, 'regressed': 1}
  - `architecture-overview`: Δ evidence_recall_at_k=+0.000000, Δ span_coverage_at_k=+0.000000, Δ evidence_reciprocal_rank=-0.088889
- **qdrant-hybrid**: {'improved': 3, 'unchanged': 14, 'regressed': 3}
  - `ai-queue-subject-context`: Δ evidence_recall_at_k=+0.000000, Δ span_coverage_at_k=-0.144445, Δ evidence_reciprocal_rank=+0.000000
  - `architecture-overview`: Δ evidence_recall_at_k=+0.000000, Δ span_coverage_at_k=+0.000000, Δ evidence_reciprocal_rank=-0.166667
  - `medication-create-controller-validation`: Δ evidence_recall_at_k=+0.000000, Δ span_coverage_at_k=+0.000000, Δ evidence_reciprocal_rank=-0.190476

All query deltas and absolute operational costs: [comparison.json](comparison.json).
Translation diagnostics, runtime telemetry and shared evaluator observations are separate artifact fields.
No time-to-first-candidate metric is available from the current runtime.
