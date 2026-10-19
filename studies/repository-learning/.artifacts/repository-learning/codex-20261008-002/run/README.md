# Repository Learning Benchmark

- Protocol: `fetchcore.repository-learning-v1`
- Scenario: `fetchcore-self-architecture`
- Repository: `oalangomes/fetchcore`
- Revision: `e0145894d5b1c0f74c3ba8c9b228556ea7d6d81c`

| Arm | FactRecall | FactPrecision | Questions | Memory pages | Tool calls | Returned chars | Wall ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| direct | 0.167 | 0.167 | 1/6 | 1 | 2 | 1079418 | 37247.1 |
| fetchcore | 0.167 | 0.167 | 1/6 | 1 | 17 | 108799 | 54537.5 |
| fetchcore_bounded | 0.500 | 0.500 | 3/6 | 3 | 6 | 19076 | 41372.1 |

## Per-question results

| Arm | Question | Result |
| --- | --- | --- |
| direct | q-canonical-knowledge | FAIL |
| direct | q-intent-scope | FAIL |
| direct | q-required-semantics | FAIL |
| direct | q-backends | FAIL |
| direct | q-hybrid | FAIL |
| direct | q-serving | PASS |
| fetchcore | q-canonical-knowledge | FAIL |
| fetchcore | q-intent-scope | FAIL |
| fetchcore | q-required-semantics | PASS |
| fetchcore | q-backends | FAIL |
| fetchcore | q-hybrid | FAIL |
| fetchcore | q-serving | FAIL |
| fetchcore_bounded | q-canonical-knowledge | PASS |
| fetchcore_bounded | q-intent-scope | FAIL |
| fetchcore_bounded | q-required-semantics | PASS |
| fetchcore_bounded | q-backends | PASS |
| fetchcore_bounded | q-hybrid | FAIL |
| fetchcore_bounded | q-serving | FAIL |

FactRecall/FactPrecision measure the durable-memory evaluation only. They are not retrieval authority scores.
