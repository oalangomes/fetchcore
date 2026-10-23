# Repository Learning Full Fresh V2 — codex-20261009-003-v2-fresh

## Identity and isolation

- Protocol: `fetchcore.repository-learning-v2`
- Mode: full fresh acquisition, memory construction, recall, and evaluation
- Scenario SHA-256: `2753adf17add395ae4977424c537f6aab13449bcc05327beb5130b60b319c302`
- Subject repository: `oalangomes/fetchcore`
- Subject revision: `e0145894d5b1c0f74c3ba8c9b228556ea7d6d81c`
- Harness revision: `22cc827c5a270986d4ff9386e2626fa6e3e2eff6`
- Harness/provider/model: `codex-cli-0.153.2` / `openai-chatgpt` / `gpt-5.6-sol`
- ai-memory: `2.6.2`, new empty data directory and unique projects for this run
- Recall: `memory_query` followed by up to three ranked unique `memory_read_page` calls
- Solr projection: benchmark core `fetchcore-rlb-e0145894-20261008-001`, validated
  `ready`, `up_to_date`, and at the frozen subject revision before execution
- Indexing fingerprint: `sha256:47f0a22468cd1f458af829063680a23f4376f2883ff75a5f24b87d9d0aee40de`
- Projection fingerprint: `sha256:cfd4a63f85c8e4034bf17f0f82f08a47c717d52e75a9180ffb1eb6cad719e039`

The benchmark-specific search projection was reused after currentness validation;
all three agent acquisitions, study memories, ai-memory projects, recalls, and
evaluator sessions were fresh. No V1/V2 replay memory was reused.

## Result

| Arm | StoredFactRecall | MemoryRecallCoverage | AnswerFactRecall | Tool calls | Files read | Evidence items | Returned chars | Input tokens | Output tokens | Wall time |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| direct | 6/6 | 6/6 | 6/6 | 4 | 238 | 238 | 83,639 | 108,112 | 2,640 | 49.916 s |
| fetchcore | 6/6 | 6/6 | 5/6 | 15 | 4 | 38 | 122,165 | 171,472 | 2,518 | 67.445 s |
| fetchcore_bounded | 5/6 | 5/6 | 4/6 | 4 | 0 | 10 | 16,496 | 51,859 | 1,416 | 38.243 s |

| Arm | Memory queries | Full pages read | Distinct pages read | Memory context chars |
| --- | ---: | ---: | ---: | ---: |
| direct | 6 | 6 | 1 | 53,683 |
| fetchcore | 6 | 6 | 1 | 48,495 |
| fetchcore_bounded | 6 | 12 | 2 | 37,061 |

The bounded arm returned about 5.1 times fewer repository characters than direct
and used about 2.1 times fewer input tokens in this fresh run. This differs sharply
from the inherited V1 acquisition ratio in the controlled replay (about 56 times
fewer characters), demonstrating that raw-evidence compression and token cost vary
independently across fresh agent executions.

For this six-fact scenario, stored-fact density per 1,000 returned repository
characters was approximately 0.072 direct, 0.049 FetchCore, and 0.303 bounded.
This derived density is descriptive only and is not a quality multiplier.

## Comparison with the canonical controlled replay

| Arm | Controlled replay (stored / recalled / answer) | Full fresh V2 |
| --- | ---: | ---: |
| direct | 5/6 / 5/6 / 5/6 | 6/6 / 6/6 / 6/6 |
| fetchcore | 6/6 / 6/6 / 5/6 | 6/6 / 6/6 / 5/6 |
| fetchcore_bounded | 5/6 / 5/6 / 4/6 | 5/6 / 5/6 / 4/6 |

The FetchCore arms reproduced the controlled replay's original-ruler stage
scores. Direct improved by one fact at every stage, showing normal fresh-study
variance. The remaining fresh FAILs occur at `fetchcore/q-required-semantics`,
`fetchcore_bounded/q-required-semantics`, and
`fetchcore_bounded/q-serving`; they must not be attributed to retrieval without a
separate semantic audit of these new texts.

## Execution command

```bash
python benchmarks/repository-learning/run.py \
  --scenario benchmarks/repository-learning/scenarios/fetchcore-self.yaml \
  --workspace /home/operator/projetos/GitHub/fetchcore-rlb-subject \
  --output /tmp/codex-20261009-003-v2-fresh \
  --run-id codex-20261009-003-v2-fresh \
  --harness-id codex-cli-0.153.2 \
  --provider-id openai-chatgpt \
  --model-id gpt-5.6-sol \
  --memory-server-url http://127.0.0.1:49376 \
  --timeout-seconds 1800 \
  --study-command 'direct=python /home/operator/projetos/GitHub/fetchcore/benchmarks/repository-learning/wrappers/codex_harness.py direct --model gpt-5.6-sol --revision e0145894d5b1c0f74c3ba8c9b228556ea7d6d81c --solr-collection fetchcore-rlb-e0145894-20261008-001' \
  --study-command 'fetchcore=python /home/operator/projetos/GitHub/fetchcore/benchmarks/repository-learning/wrappers/codex_harness.py fetchcore --model gpt-5.6-sol --revision e0145894d5b1c0f74c3ba8c9b228556ea7d6d81c --solr-collection fetchcore-rlb-e0145894-20261008-001' \
  --study-command 'fetchcore_bounded=python /home/operator/projetos/GitHub/fetchcore/benchmarks/repository-learning/wrappers/codex_harness.py fetchcore-bounded --model gpt-5.6-sol --revision e0145894d5b1c0f74c3ba8c9b228556ea7d6d81c --solr-collection fetchcore-rlb-e0145894-20261008-001' \
  --evaluate-command 'python /home/operator/projetos/GitHub/fetchcore/benchmarks/repository-learning/wrappers/codex_harness.py evaluate --model gpt-5.6-sol --revision e0145894d5b1c0f74c3ba8c9b228556ea7d6d81c --solr-collection fetchcore-rlb-e0145894-20261008-001'
```

