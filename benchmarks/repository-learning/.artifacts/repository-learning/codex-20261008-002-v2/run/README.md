# Repository Learning Benchmark

- Protocol: `fetchcore.repository-learning-v2`
- Scenario: `fetchcore-self-architecture`
- Repository: `oalangomes/fetchcore`
- Revision: `e0145894d5b1c0f74c3ba8c9b228556ea7d6d81c`

| Arm | StoredFactRecall | MemoryRecallCoverage | AnswerFactRecall | Questions | Memory pages | Tool calls | Returned chars | Wall ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| direct | 0.833 | 0.833 | 0.833 | 5/6 | 1 | 2 | 1079418 | 35653.5 |
| fetchcore | 1.000 | 1.000 | 0.833 | 5/6 | 1 | 17 | 108799 | 14859.2 |
| fetchcore_bounded | 0.833 | 0.833 | 0.667 | 4/6 | 3 | 6 | 19076 | 28599.6 |

## Per-question stage results

| Arm | Question | Stored | Recalled | Answer |
| --- | --- | --- | --- | --- |
| direct | q-canonical-knowledge | FAIL | FAIL | FAIL |
| direct | q-intent-scope | PASS | PASS | PASS |
| direct | q-required-semantics | PASS | PASS | PASS |
| direct | q-backends | PASS | PASS | PASS |
| direct | q-hybrid | PASS | PASS | PASS |
| direct | q-serving | PASS | PASS | PASS |
| fetchcore | q-canonical-knowledge | PASS | PASS | FAIL |
| fetchcore | q-intent-scope | PASS | PASS | PASS |
| fetchcore | q-required-semantics | PASS | PASS | PASS |
| fetchcore | q-backends | PASS | PASS | PASS |
| fetchcore | q-hybrid | PASS | PASS | PASS |
| fetchcore | q-serving | PASS | PASS | PASS |
| fetchcore_bounded | q-canonical-knowledge | PASS | PASS | PASS |
| fetchcore_bounded | q-intent-scope | PASS | PASS | FAIL |
| fetchcore_bounded | q-required-semantics | PASS | PASS | PASS |
| fetchcore_bounded | q-backends | PASS | PASS | PASS |
| fetchcore_bounded | q-hybrid | PASS | PASS | PASS |
| fetchcore_bounded | q-serving | FAIL | FAIL | FAIL |

StoredFactRecall measures facts present in the durable pages before ai-memory retrieval. MemoryRecallCoverage measures the query→read_page recall context. AnswerFactRecall measures the fresh evaluator output. None is an authority score.

> Re-evaluation mode: study/acquisition artifacts were reused from the source run. Wall time covers memory replay and fresh evaluation only.
