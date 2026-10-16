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
| solr-dense | 0.631587 | 0.706587 | 0.735754 | 0.748254 |
| solr-hybrid | 0.492857 | 0.625000 | 0.687500 | 0.705952 |
| opensearch-lexical | 0.225000 | 0.241667 | 0.316667 | 0.316667 |
| opensearch-dense | 0.631587 | 0.806587 | 0.835754 | 0.848254 |
| opensearch-hybrid | 0.442857 | 0.725000 | 0.793452 | 0.814841 |
| qdrant-dense | 0.631587 | 0.806587 | 0.835754 | 0.848254 |
| qdrant-hybrid | 0.433333 | 0.750556 | 0.856587 | 0.894087 |

## Budgeted evidence recall

| Arm | 100L | 250L | 500L | 1000L |
| --- | ---: | ---: | ---: | ---: |
| local-lexical | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| solr-lexical | 0.216667 | 0.258333 | 0.333333 | 0.333333 |
| solr-dense | 0.600000 | 0.675000 | 0.704167 | 0.716667 |
| solr-hybrid | 0.475000 | 0.625000 | 0.687500 | 0.700000 |
| opensearch-lexical | 0.225000 | 0.241667 | 0.316667 | 0.316667 |
| opensearch-dense | 0.600000 | 0.775000 | 0.804167 | 0.816667 |
| opensearch-hybrid | 0.425000 | 0.725000 | 0.787500 | 0.800000 |
| qdrant-dense | 0.600000 | 0.775000 | 0.804167 | 0.816667 |
| qdrant-hybrid | 0.433333 | 0.741667 | 0.825000 | 0.862500 |

## Packed-context diagnostics

| Arm | 100L | 250L | 500L | 1000L |
| --- | ---: | ---: | ---: | ---: |
| local-lexical | 24.3 lines / 7.3 results | 24.3 lines / 7.3 results | 24.3 lines / 7.3 results | 24.3 lines / 7.3 results |
| solr-lexical | 67.2 lines / 5.8 results | 202.7 lines / 11.9 results | 364.4 lines / 14.8 results | 537.5 lines / 17.9 results |
| solr-dense | 59.4 lines / 3.5 results | 193.1 lines / 8.6 results | 404.1 lines / 15.8 results | 552.7 lines / 18.1 results |
| solr-hybrid | 56.6 lines / 3.5 results | 203.2 lines / 8.2 results | 428.4 lines / 14.7 results | 638.2 lines / 19.1 results |
| opensearch-lexical | 72.5 lines / 5.9 results | 188.1 lines / 11.8 results | 357.4 lines / 15.1 results | 539.9 lines / 18.3 results |
| opensearch-dense | 59.4 lines / 3.5 results | 184.2 lines / 8.3 results | 401.7 lines / 14.4 results | 603.9 lines / 17.9 results |
| opensearch-hybrid | 55.1 lines / 3.3 results | 192.4 lines / 8.0 results | 427.8 lines / 14.0 results | 675.5 lines / 19.6 results |
| qdrant-dense | 59.4 lines / 3.5 results | 182.8 lines / 8.3 results | 401.9 lines / 14.3 results | 609.4 lines / 17.9 results |
| qdrant-hybrid | 44.5 lines / 1.8 results | 191.1 lines / 4.0 results | 431.8 lines / 8.9 results | 858.0 lines / 16.3 results |

ContextYield is budgeted span coverage, not consumer satisfaction or semantic authority.
