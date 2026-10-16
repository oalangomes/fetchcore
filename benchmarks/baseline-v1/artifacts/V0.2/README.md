# Baseline V0.2 metrics

This report is generated from the aggregate fields in this run's JSON
artifacts. The JSON files remain the source of truth.

## Run

- Protocol: `fetchcore.baseline-v1`
- Status: `complete`
- Completed at: `2026-10-15T23:24:01.208696+00:00`
- FetchCore revision: `3cd62ba28a6da2a716711e867d0162f15f9c9076`
- Corpus: `oalangomes/NeuroTrack_MS@74ce3244956a65baed61f1dae6f498cb2be4f3b7`

## Retrieval quality and latency

| Arm | Source recall@K | Evidence recall@K | Span coverage@K | Evidence MRR | Evidence nDCG@K | Useful context | p50 | p95 | p99 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| [local-lexical](local-lexical.json) | 0.350000 | 0.000000 | 0.070563 | 0.118333 | 0.158484 | 0.023448 | 2455 ms | 3414 ms | 4526 ms |
| [solr-lexical](solr-lexical.json) | 0.500000 | 0.333333 | 0.333333 | 0.187500 | 0.252076 | 0.010586 | 39 ms | 59 ms | 74 ms |
| [solr-dense](solr-dense.json) | 0.900000 | 0.816667 | 0.848254 | 0.745833 | 0.760060 | 0.047813 | 51 ms | 86 ms | 98 ms |
| [solr-hybrid](solr-hybrid.json) | 0.900000 | 0.800000 | 0.822064 | 0.629167 | 0.661998 | 0.036934 | 61 ms | 95 ms | 112 ms |
| [opensearch-lexical](opensearch-lexical.json) | 0.450000 | 0.316667 | 0.316667 | 0.191346 | 0.240226 | 0.010205 | 46 ms | 90 ms | 117 ms |
| [opensearch-dense](opensearch-dense.json) | 0.900000 | 0.816667 | 0.848254 | 0.745833 | 0.760060 | 0.047699 | 64 ms | 104 ms | 136 ms |
| [opensearch-hybrid](opensearch-hybrid.json) | 0.900000 | 0.800000 | 0.814841 | 0.583333 | 0.638009 | 0.037744 | 94 ms | 154 ms | 199 ms |
| [qdrant-dense](qdrant-dense.json) | 0.900000 | 0.816667 | 0.848254 | 0.745833 | 0.759359 | 0.047448 | 1046 ms | 1631 ms | 1977 ms |
| [qdrant-hybrid](qdrant-hybrid.json) | 0.950000 | 0.900000 | 0.931587 | 0.787500 | 0.798024 | 0.034401 | 1367 ms | 1771 ms | 2083 ms |

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
| qdrant-hybrid | 400 | 20 | 138.5 | 20 | 64412.4 | 1173.15 | 760 |

## Indexing

Index preparation was not requested for this run.

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
