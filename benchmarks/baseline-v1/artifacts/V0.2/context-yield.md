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
| solr-hybrid | 0.517857 | 0.750000 | 0.787500 | 0.814841 |
| opensearch-lexical | 0.225000 | 0.241667 | 0.316667 | 0.316667 |
| opensearch-dense | 0.631587 | 0.806587 | 0.835754 | 0.848254 |
| opensearch-hybrid | 0.467857 | 0.750000 | 0.793452 | 0.814841 |
| qdrant-dense | 0.631587 | 0.806587 | 0.835754 | 0.848254 |
| qdrant-hybrid | 0.492222 | 0.750556 | 0.856587 | 0.894087 |

## Budgeted evidence recall

| Arm | 100L | 250L | 500L | 1000L |
| --- | ---: | ---: | ---: | ---: |
| local-lexical | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| solr-lexical | 0.216667 | 0.258333 | 0.333333 | 0.333333 |
| solr-dense | 0.600000 | 0.775000 | 0.804167 | 0.816667 |
| solr-hybrid | 0.500000 | 0.750000 | 0.787500 | 0.800000 |
| opensearch-lexical | 0.225000 | 0.241667 | 0.316667 | 0.316667 |
| opensearch-dense | 0.600000 | 0.775000 | 0.804167 | 0.816667 |
| opensearch-hybrid | 0.450000 | 0.750000 | 0.787500 | 0.800000 |
| qdrant-dense | 0.600000 | 0.775000 | 0.804167 | 0.816667 |
| qdrant-hybrid | 0.483333 | 0.741667 | 0.825000 | 0.862500 |

## Packed-context diagnostics

| Arm | 100L | 250L | 500L | 1000L |
| --- | ---: | ---: | ---: | ---: |
| local-lexical | 24.3 lines / 7.3 results | 24.3 lines / 7.3 results | 24.3 lines / 7.3 results | 24.3 lines / 7.3 results |
| solr-lexical | 66.8 lines / 5.7 results | 198.4 lines / 11.9 results | 371.8 lines / 15.0 results | 535.9 lines / 18.0 results |
| solr-dense | 63.0 lines / 4.5 results | 175.7 lines / 8.2 results | 395.4 lines / 14.3 results | 597.8 lines / 17.9 results |
| solr-hybrid | 61.1 lines / 4.0 results | 197.8 lines / 8.2 results | 410.6 lines / 13.6 results | 669.3 lines / 19.2 results |
| opensearch-lexical | 73.4 lines / 6.0 results | 186.8 lines / 11.8 results | 357.2 lines / 15.1 results | 538.0 lines / 18.2 results |
| opensearch-dense | 63.0 lines / 4.5 results | 175.7 lines / 8.2 results | 395.7 lines / 14.4 results | 597.9 lines / 17.9 results |
| opensearch-hybrid | 60.0 lines / 4.0 results | 186.1 lines / 8.1 results | 411.8 lines / 13.9 results | 675.2 lines / 19.6 results |
| qdrant-dense | 59.4 lines / 3.6 results | 180.9 lines / 8.3 results | 400.3 lines / 14.3 results | 607.8 lines / 17.9 results |
| qdrant-hybrid | 48.2 lines / 1.9 results | 190.4 lines / 4.0 results | 426.1 lines / 8.8 results | 864.5 lines / 16.3 results |

ContextYield is budgeted span coverage, not consumer satisfaction or semantic authority.
