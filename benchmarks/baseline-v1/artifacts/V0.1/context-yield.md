# Baseline context yield

Derived offline from frozen ranked result spans and evidence-region Ground Truth.
No retrieval backend is invoked by this report.

- Protocol: `fetchcore.baseline-v1-context-yield`
- Source protocol: `fetchcore.baseline-v1`
- Metrics fingerprint: `sha256:f021672682b6cee1ae420636b567924321ab65ab894580be738ab0241d17b693`
- Budget fingerprint: `sha256:31303d7a762466fdeeeae2fa148d5e10f8175cad6614735d84e9fcbafd0daeac`

## ContextYield by line budget

| Arm | 100L | 250L | 500L | 1000L |
| --- | ---: | ---: | ---: | ---: |
| local-lexical | 0.070563 | 0.070563 | 0.070563 | 0.070563 |
| solr-lexical | 0.216667 | 0.258333 | 0.333333 | 0.333333 |
| solr-dense | 0.631587 | 0.806587 | 0.835754 | 0.848254 |
| solr-hybrid | 0.481015 | 0.750000 | 0.787500 | 0.814841 |
| opensearch-lexical | 0.225000 | 0.241667 | 0.291667 | 0.316667 |
| opensearch-dense | 0.631587 | 0.806587 | 0.835754 | 0.848254 |
| opensearch-hybrid | 0.431015 | 0.750000 | 0.793452 | 0.814841 |
| qdrant-dense | 0.631587 | 0.806587 | 0.835754 | 0.848254 |
| qdrant-hybrid | 0.442222 | 0.750556 | 0.856587 | 0.919087 |

## Budgeted evidence recall

| Arm | 100L | 250L | 500L | 1000L |
| --- | ---: | ---: | ---: | ---: |
| local-lexical | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| solr-lexical | 0.216667 | 0.258333 | 0.333333 | 0.333333 |
| solr-dense | 0.600000 | 0.775000 | 0.804167 | 0.816667 |
| solr-hybrid | 0.450000 | 0.750000 | 0.787500 | 0.800000 |
| opensearch-lexical | 0.225000 | 0.241667 | 0.291667 | 0.316667 |
| opensearch-dense | 0.600000 | 0.775000 | 0.804167 | 0.816667 |
| opensearch-hybrid | 0.400000 | 0.750000 | 0.787500 | 0.800000 |
| qdrant-dense | 0.600000 | 0.775000 | 0.804167 | 0.816667 |
| qdrant-hybrid | 0.433333 | 0.741667 | 0.825000 | 0.887500 |

## Packed-context diagnostics

| Arm | 100L | 250L | 500L | 1000L |
| --- | ---: | ---: | ---: | ---: |
| local-lexical | 24.6 lines / 7.4 results | 24.6 lines / 7.4 results | 24.6 lines / 7.4 results | 24.6 lines / 7.4 results |
| solr-lexical | 66.8 lines / 5.7 results | 197.2 lines / 11.6 results | 374.9 lines / 14.3 results | 608.0 lines / 18.4 results |
| solr-dense | 63.2 lines / 4.5 results | 174.8 lines / 8.2 results | 402.4 lines / 13.7 results | 647.0 lines / 17.6 results |
| solr-hybrid | 59.5 lines / 3.9 results | 203.1 lines / 8.3 results | 414.9 lines / 12.8 results | 704.5 lines / 18.9 results |
| opensearch-lexical | 73.4 lines / 6.0 results | 189.6 lines / 11.4 results | 360.1 lines / 14.3 results | 608.5 lines / 18.5 results |
| opensearch-dense | 63.2 lines / 4.5 results | 175.2 lines / 8.2 results | 402.7 lines / 13.7 results | 634.5 lines / 17.6 results |
| opensearch-hybrid | 58.4 lines / 3.8 results | 190.8 lines / 8.2 results | 418.4 lines / 13.2 results | 712.1 lines / 19.4 results |
| qdrant-dense | 59.4 lines / 3.6 results | 180.0 lines / 8.2 results | 396.9 lines / 13.5 results | 643.6 lines / 17.6 results |
| qdrant-hybrid | 44.5 lines / 1.8 results | 194.6 lines / 4.2 results | 431.4 lines / 8.8 results | 881.3 lines / 15.4 results |

ContextYield is budgeted span coverage, not consumer satisfaction or semantic authority.
