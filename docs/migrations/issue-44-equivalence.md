# Issue #44 compatibility and equivalence record

The migration moved existing implementations by responsibility. It did not
change SearchIntent, expansion, ranking, RRF, reranking, chunking, candidate
budgets, backend query semantics, scoring, or the frozen #18 protocol.

## Persisted identities

An unmodified `git archive HEAD` of `src/fetchcore` was loaded in a separate
Python process and compared with the reorganized source. All six values matched:

| Identity | Before and after |
| --- | --- |
| Projection contract | `sha256:cfd4a63f85c8e4034bf17f0f82f08a47c717d52e75a9180ffb1eb6cad719e039` |
| Embedding serializer | `sha256:5f859289b8b3a594e924545edb1c3ae6178b77ea22cf3ff224d43c5ec7aeb43a` |
| Default indexer | `sha256:efd070e46ed23158fd7b8a294b1c3303088045b6e320601a3c0ca8a385b57084` |
| Structural indexer | `sha256:41f42d051e2c68b16f220eee761f504e4918a7fb58600d0475d48fe0993121d7` |
| Document indexer | `sha256:07e51fb885aeb93befcf67c55cb1a8c3103a2c4475293cc9a09741c32aa4dfe0` |
| Prepared repository runtime | `sha256:0edd5af77860146b2027e69674c798aafcf74b0f517d8a8adf6d3702776f9e3e` |

Moved extractor classes retain their historical `__module__` names because
those names participate in persisted indexing fingerprints. Projection version
`3`, embedding serialization version `2`, indexing contract version `23`,
policy paths/versions, and the MCP script entry point remain unchanged.
The SAP Commerce knowledge pack, interpreter, seven enrichers, and resolver
also retain their exact pre-migration class identity strings.

## Frozen #18 ranking check

The checked-out private corpus `oalangomes/NeuroTrack_MS` was clean at
`74ce3244956a65baed61f1dae6f498cb2be4f3b7`. A single current retrieval
pass for every frozen query in every arm was compared to the V0.1 artifact's
first observed rank, result ID, source reference, and line span:

| Arm | Exact rankings |
| --- | ---: |
| local-lexical | 20/20 |
| solr-lexical, solr-dense, solr-hybrid | 60/60 |
| opensearch-lexical, opensearch-dense, opensearch-hybrid | 60/60 |
| qdrant-dense, qdrant-hybrid | 40/40 |
| **Total** | **180/180** |

Reproduce with:

```bash
.venv/bin/python benchmarks/baseline-v1/compare_rankings.py \
  --workspace /home/alangomes/projetos/GitHub/NeuroTrack_MS
```

This is a ranking/evidence equivalence check. It is not a new 20-repetition
latency measurement; timing and cost in V0.1 remain the frozen #18 baseline.
Remote services and their active snapshots must be available for the eight
remote arms. The script fails explicitly if a backend or corpus is unavailable
or incompatible.

## Remaining structural debt

The backend stores still combine storage lifecycle, search execution, and
diagnostics in large files. Vendor query translation and Solr synchronization
now have dedicated modules; further store splitting would touch active
projection lifecycles without a clearer contract seam. Repository `indexing.py`
still combines canonical document types with the indexer, and `structure.py`
contains several built-in structural extractors. Their existing cyclic-risk
late registration is confined to the new extractor registry boundary. Neither
was rewritten as part of this issue.

SearchIntent v2/#19, targeted retrieval, adaptive budgets, new backends,
providers, graph behavior, and retrieval tuning are outside this migration.
