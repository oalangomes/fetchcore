# Baseline V0.1 metrics

This report is generated from the aggregate fields in this run's JSON
artifacts. The JSON files remain the source of truth.

## Run

- Protocol: `fetchcore.baseline-v1`
- Status: `complete`
- Completed at: `2026-10-01T15:09:18-03:00`
- FetchCore revision: `e5b364a0f978186c47911246298f7c9fcc6c1631`
- Corpus: `oalangomes/NeuroTrack_MS@74ce3244956a65baed61f1dae6f498cb2be4f3b7`

## Retrieval quality and latency

| Arm | Source recall@K | Evidence recall@K | Span coverage@K | Evidence MRR | Evidence nDCG@K | Useful context | p50 | p95 | p99 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| [local-lexical](local-lexical.json) | 0.350000 | 0.000000 | 0.070563 | 0.118333 | 0.158484 | 0.023448 | 2993 ms | 3775 ms | 3972 ms |
| [solr-lexical](solr-lexical.json) | 0.500000 | 0.333333 | 0.333333 | 0.187179 | 0.251697 | 0.009964 | 17 ms | 28 ms | 33 ms |
| [solr-dense](solr-dense.json) | 0.900000 | 0.816667 | 0.848254 | 0.746212 | 0.759794 | 0.046348 | 33 ms | 46 ms | 54 ms |
| [solr-hybrid](solr-hybrid.json) | 0.900000 | 0.800000 | 0.822064 | 0.629167 | 0.660983 | 0.036357 | 52 ms | 70 ms | 78 ms |
| [opensearch-lexical](opensearch-lexical.json) | 0.500000 | 0.316667 | 0.316667 | 0.191071 | 0.239891 | 0.009719 | 28 ms | 40 ms | 48 ms |
| [opensearch-dense](opensearch-dense.json) | 0.900000 | 0.816667 | 0.848254 | 0.745833 | 0.759359 | 0.046475 | 42 ms | 61 ms | 68 ms |
| [opensearch-hybrid](opensearch-hybrid.json) | 0.900000 | 0.800000 | 0.814841 | 0.583333 | 0.636993 | 0.037125 | 60 ms | 83 ms | 91 ms |
| [qdrant-dense](qdrant-dense.json) | 0.900000 | 0.816667 | 0.848254 | 0.745833 | 0.759359 | 0.046606 | 994 ms | 1069 ms | 1130 ms |
| [qdrant-hybrid](qdrant-hybrid.json) | 0.950000 | 0.900000 | 0.931587 | 0.770513 | 0.779853 | 0.033132 | 1316 ms | 1458 ms | 1548 ms |

## Retrieval volume

| Arm | Observations | Queries | Candidates examined | Candidates returned | Returned chars | Returned lines | Ground Truth recovered |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| local-lexical | 400 | 20 | 13.1 | 7.4 | 1031.45 | 24.6 | 0 |
| solr-lexical | 400 | 20 | 76.6 | 19.6 | 30263.8 | 758.9 | 240 |
| solr-dense | 400 | 20 | 80 | 20 | 29525.1 | 888.45 | 680 |
| solr-hybrid | 400 | 20 | 37.35 | 20 | 28578.75 | 775.15 | 660 |
| opensearch-lexical | 400 | 20 | 76.6 | 19.6 | 31405.35 | 754.75 | 220 |
| opensearch-dense | 400 | 20 | 80 | 20 | 29530.55 | 890.9 | 680 |
| opensearch-hybrid | 400 | 20 | 37.3 | 20 | 27937.8 | 743 | 660 |
| qdrant-dense | 400 | 20 | 80 | 20 | 29372.95 | 875.1 | 680 |
| qdrant-hybrid | 400 | 20 | 138.75 | 20 | 67598.55 | 1271.05 | 760 |

## Indexing

- Files discovered: 1189
- Files indexed: 894
- Chunks/documents: 9620
- Canonical duration: 12359 ms
- Canonical throughput: 778.38 chunks/s

| Backend | Mode | Duration | Writes | Deletes | Embedding duration | Vectors | Vector throughput |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| opensearch | full_rebuild | 320715 ms | 9620 | 0 | 305502 ms | 9620 | 31.489 vectors/s |
| qdrant | full_rebuild | 318733 ms | 9620 | 0 | 301808 ms | 9620 | 31.875 vectors/s |
| solr | full_rebuild | 770005 ms | 9620 | 9620 | 305202 ms | 9620 | 31.52 vectors/s |

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
- [indexing.json](indexing.json)
