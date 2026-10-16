# Baseline V0.3 metrics

This report is generated from the aggregate fields in this run's JSON
artifacts. The JSON files remain the source of truth.

## Run

- Protocol: `fetchcore.baseline-v1`
- Status: `complete`
- Completed at: `2026-10-16T14:28:24.601042+00:00`
- FetchCore revision: `36fcec3f13091a7a583d820adce813f9bb77db3f`
- Corpus: `oalangomes/NeuroTrack_MS@74ce3244956a65baed61f1dae6f498cb2be4f3b7`

## Retrieval quality and latency

| Arm | Source recall@K | Evidence recall@K | Span coverage@K | Evidence MRR | Evidence nDCG@K | Useful context | p50 | p95 | p99 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| [local-lexical](local-lexical.json) | 0.350000 | 0.000000 | 0.070563 | 0.118333 | 0.158484 | 0.023448 | 2713 ms | 3464 ms | 3990 ms |
| [solr-lexical](solr-lexical.json) | 0.550000 | 0.333333 | 0.333333 | 0.187500 | 0.252076 | 0.010587 | 24 ms | 40 ms | 52 ms |
| [solr-dense](solr-dense.json) | 0.800000 | 0.716667 | 0.748254 | 0.645513 | 0.658979 | 0.046334 | 41 ms | 62 ms | 77 ms |
| [solr-hybrid](solr-hybrid.json) | 0.800000 | 0.700000 | 0.722064 | 0.562500 | 0.581932 | 0.034944 | 62 ms | 92 ms | 117 ms |
| [opensearch-lexical](opensearch-lexical.json) | 0.450000 | 0.316667 | 0.316667 | 0.191346 | 0.240226 | 0.010182 | 39 ms | 66 ms | 86 ms |
| [opensearch-dense](opensearch-dense.json) | 0.900000 | 0.816667 | 0.848254 | 0.745513 | 0.758979 | 0.047672 | 50 ms | 85 ms | 112 ms |
| [opensearch-hybrid](opensearch-hybrid.json) | 0.900000 | 0.800000 | 0.814841 | 0.595833 | 0.641845 | 0.037836 | 77 ms | 115 ms | 145 ms |
| [qdrant-dense](qdrant-dense.json) | 0.900000 | 0.816667 | 0.848254 | 0.745513 | 0.758979 | 0.046935 | 910 ms | 1030 ms | 1137 ms |
| [qdrant-hybrid](qdrant-hybrid.json) | 0.950000 | 0.900000 | 0.931587 | 0.764583 | 0.781553 | 0.034036 | 1236 ms | 1378 ms | 1413 ms |

## Retrieval volume

| Arm | Observations | Queries | Candidates examined | Candidates returned | Returned chars | Returned lines | Ground Truth recovered |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| local-lexical | 400 | 20 | 13 | 7.3 | 1027.05 | 24.3 | 0 |
| solr-lexical | 400 | 20 | 75.4 | 19.4 | 26301.2 | 644 | 240 |
| solr-dense | 400 | 20 | 80 | 20 | 24989.65 | 732.15 | 620 |
| solr-hybrid | 400 | 20 | 37.15 | 20 | 26838.3 | 707.1 | 600 |
| opensearch-lexical | 400 | 20 | 75.4 | 19.4 | 28765.3 | 670.35 | 220 |
| opensearch-dense | 400 | 20 | 80 | 20 | 27516.75 | 807.45 | 680 |
| opensearch-hybrid | 400 | 20 | 37.3 | 20 | 27538.65 | 704.05 | 660 |
| qdrant-dense | 400 | 20 | 80 | 20 | 27963.3 | 813 | 680 |
| qdrant-hybrid | 400 | 20 | 138.25 | 20 | 64118.4 | 1180 | 760 |

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
