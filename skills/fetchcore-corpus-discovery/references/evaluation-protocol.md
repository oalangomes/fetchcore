# Future selection-policy evaluation

Status: proposed protocol; no reindexing or new retrieval benchmark executed for
the skill implementation. A smaller corpus is not evidence of better retrieval.

Freeze the same repository revision/corpus, backend/schema, lexical retrieval
profile and budgets, question set and evidence Ground Truth where available.
Record policy checksums, projection/serializer/provider fingerprints and all
relevant configuration. Use isolated operator-approved experimental indexes.
One experimental variable: selection policy. Do not tune ranking, chunking,
embeddings, reranking or candidate windows in the comparison.

Predeclare the hypothesis: narrowly removing replayable historical evaluation
outputs may improve useful context density while preserving architecture,
contracts, implementation and test evidence coverage. Possible regression:
historical/reproducibility questions lose unique evidence.

Measure per question and aggregate, with uncertainty and explicit units:

- EvidenceRecall@K against the frozen evidence GT, including evidence lost.
- UsefulContextLineRatio using the same frozen annotation method/denominator.
- ContextYield@B using identical context budgets and the existing metric protocol.
- Returned characters, candidate counts and truncation.
- Latency (p50/p95 when repetitions support it), time to first candidate and cost.
- Indexing throughput/writes and bytes if a separately authorized experiment measures them.

Use lexical as the baseline required by issue #217. If evaluating another retrieval
profile later, freeze that profile across A/B and report it separately. Record raw
artifacts, question-level regressions and any changed coverage. Keep LLM usefulness
annotations explicitly heuristic, separate from canonical evidence GT.

Do not adopt a default solely from fewer files, lower returned volume or the issue's
exploratory 58293-character observation. Report measured effects and regressions
before a separate human decision about policy adoption or index rebuild.
