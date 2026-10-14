# Baseline V0.2 metrics

This report is generated from the aggregate fields in this run's JSON
artifacts. The JSON files remain the source of truth.

## Run

- Protocol: `fetchcore.baseline-v1`
- Status: `retrieval_complete`
- Completed at: `2026-10-14T15:08:07.147729+00:00`
- FetchCore revision: `3888cffd30f01321982d3607cbdac88bbb8b4de4`
- Corpus: `oalangomes/NeuroTrack_MS@74ce3244956a65baed61f1dae6f498cb2be4f3b7`

## Retrieval quality and latency

| Arm | Source recall@K | Evidence recall@K | Span coverage@K | Evidence MRR | Evidence nDCG@K | Useful context | p50 | p95 | p99 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| [local-lexical](local-lexical.json) | 0.350000 | 0.000000 | 0.070563 | 0.118333 | 0.158484 | 0.023448 | 2135 ms | 2651 ms | 2783 ms |
| [solr-lexical](solr-lexical.json) | 0.500000 | 0.333333 | 0.333333 | 0.187500 | 0.252076 | 0.010586 | 17 ms | 23 ms | 26 ms |
| [solr-dense](solr-dense.json) | 0.900000 | 0.816667 | 0.848254 | 0.745833 | 0.760060 | 0.047813 | 35 ms | 51 ms | 65 ms |
| [solr-hybrid](solr-hybrid.json) | 0.900000 | 0.800000 | 0.822064 | 0.629167 | 0.661998 | 0.036934 | 55 ms | 76 ms | 92 ms |
| [opensearch-lexical](opensearch-lexical.json) | 0.450000 | 0.316667 | 0.316667 | 0.191346 | 0.240226 | 0.010205 | 29 ms | 49 ms | 61 ms |
| [opensearch-dense](opensearch-dense.json) | 0.900000 | 0.816667 | 0.848254 | 0.745833 | 0.760060 | 0.047699 | 45 ms | 63 ms | 78 ms |
| [opensearch-hybrid](opensearch-hybrid.json) | 0.900000 | 0.800000 | 0.814841 | 0.583333 | 0.638009 | 0.037744 | 68 ms | 94 ms | 104 ms |
| [qdrant-dense](qdrant-dense.json) | 0.900000 | 0.816667 | 0.848254 | 0.745833 | 0.759359 | 0.047448 | 891 ms | 1105 ms | 1245 ms |
| [qdrant-hybrid](qdrant-hybrid.json) | 0.950000 | 0.900000 | 0.931587 | 0.795513 | 0.800531 | 0.034241 | 1325 ms | 1497 ms | 1598 ms |

## Retrieval volume

| Arm | Observations | Queries | Candidates examined | Candidates returned | Returned chars | Returned lines | Ground Truth recovered |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| local-lexical | 400 | 20 | 13 | 7.3 | 1027.05 | 24.3 | 0 |
| solr-lexical | 400 | 20 | 75.45 | 19.45 | 27142.6 | 656.45 | 240 |
| solr-dense | 400 | 20 | 80 | 20 | 27180.35 | 797.3 | 680 |
| solr-hybrid | 400 | 20 | 37.2 | 20 | 28469.35 | 737.6 | 660 |
| opensearch-lexical | 400 | 20 | 75.45 | 19.45 | 29000.7 | 670.35 | 220 |
| opensearch-dense | 400 | 20 | 80 | 20 | 27295.6 | 801.45 | 680 |
| opensearch-hybrid | 400 | 20 | 37.3 | 20 | 27816.6 | 703.7 | 660 |
| qdrant-dense | 400 | 20 | 80 | 20 | 27896.9 | 811.4 | 680 |
| qdrant-hybrid | 400 | 20 | 137.9 | 20 | 64362.85 | 1176.45 | 760 |

## Indexing

Index preparation was performed before interruption, but its telemetry was not preserved. This run is not complete for baseline promotion.

## Artifacts

- [run.json](run.json)
- [local-lexical.json](local-lexical.json)
- [solr-lexical.json](solr-lexical.json)
- [solr-dense.json](solr-dense.json)
- [solr-hybrid.json](solr-hybrid.json)
- [opensearch-lexical.json](opensearch-lexical.json)
- [opensearch-dense.json](opensearch-dense.json)
- [opensearch-hybrid.json](opensearch-hybrid.json)
- [qdrant-dense.json](qdrant-dense.json)
- [qdrant-hybrid.json](qdrant-hybrid.json)
