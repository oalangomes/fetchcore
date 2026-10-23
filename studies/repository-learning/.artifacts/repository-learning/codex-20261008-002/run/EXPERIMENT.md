# Repository Learning Benchmark — codex-20261008-002

## Result

For this scenario, model, and frozen revision, **FETCHCORE_BOUNDED improved both
durable learning quality and acquisition efficiency over DIRECT**: 3/6 versus
1/6 correct, with 19,076 versus 1,079,418 evidence characters and 52,414 versus
71,523 input tokens. Unbounded FETCHCORE matched DIRECT's 1/6 quality and used
90% fewer evidence characters, but required more tool calls, input tokens, and
wall time; its efficiency result is therefore mixed.

This is a learning-pipeline result, not a retrieval authority score. Inspection
shows that all three study memories contained substantially more correct facts
than their evaluator scores. Short ai-memory recall snippets and evaluator/ruler
phrasing were the dominant downstream loss mechanisms.

## Frozen identities

- Protocol: `fetchcore.repository-learning-v1`
- Scenario: `fetchcore-self-architecture`
- Scenario SHA-256: `2753adf17add395ae4977424c537f6aab13449bcc05327beb5130b60b319c302`
- Harness revision: `962839e6613458461b45576ddc5ed9ca41a569d7`
- Subject revision: `e0145894d5b1c0f74c3ba8c9b228556ea7d6d81c`
- Codex: `codex-cli 0.153.2`, provider `openai-chatgpt`, model `gpt-5.6-sol`
- ai-memory: `2.6.2`, fresh data directory and unique project per arm
- Backend: Solr `9.10.1`, `http://127.0.0.1:8983/solr`
- Core: `fetchcore-rlb-e0145894-20261008-001`
- Repository documents: 2,746 plus one snapshot record
- Indexing fingerprint: `sha256:47f0a22468cd1f458af829063680a23f4376f2883ff75a5f24b87d9d0aee40de`
- Projection fingerprint: `sha256:cfd4a63f85c8e4034bf17f0f82f08a47c717d52e75a9180ffb1eb6cad719e039`
- Projection version: `3`
- Snapshot status/revision: `ready` / `e0145894d5b1c0f74c3ba8c9b228556ea7d6d81c`
- Study prompt SHA-256: `a70318ba0e279cb43ad035a9cdf47093c568018b0ae335122cfee4f979e4d3e3`
- Evaluation prompt SHA-256: `756f41635b73d5e8d558c379862823149f23693f101a25e88e93639ddfe570d1`

## Metrics

| Metric | DIRECT | FETCHCORE | FETCHCORE_BOUNDED |
|---|---:|---:|---:|
| FactRecall | 0.167 | 0.167 | 0.500 |
| FactPrecision | 0.167 | 0.167 | 0.500 |
| Correct questions | 1 | 1 | 3 |
| Incorrect/unsupported | 5 | 5 | 3 |
| Tool calls | 2 | 17 | 6 |
| Files read | 270 | 11 | 0 |
| Evidence items | 270 | 38 | 11 |
| Returned chars | 1,079,418 | 108,799 | 19,076 |
| Input tokens | 71,523 | 214,894 | 52,414 |
| Output tokens | 1,812 | 2,728 | 1,741 |
| Memory pages | 1 | 1 | 3 |
| Wall time | 37.247 s | 54.537 s | 41.372 s |
| Tool events verified | yes | yes | yes |

`files_read` is arm-specific: DIRECT infers repository files scanned/read from
observed read-only shell commands; FetchCore counts distinct `get_source` reads.
Search evidence is represented by `evidence_items` rather than `files_read`.

## Derived ratios

All denominators below are externally observed by the wrapper.

| Ratio | DIRECT | FETCHCORE | FETCHCORE_BOUNDED |
|---|---:|---:|---:|
| Correct / 1k returned chars | 0.0009 | 0.0092 | 0.1573 |
| Correct / 1k input tokens | 0.0140 | 0.0047 | 0.0572 |
| Correct / tool call | 0.5000 | 0.0588 | 0.5000 |

Relative to DIRECT, FETCHCORE_BOUNDED reduced evidence characters by 98.2% and
input tokens by 26.7%. Unbounded FETCHCORE reduced evidence characters by 89.9%
but used 200.5% more input tokens.

## Per-question results

| Question | DIRECT | FETCHCORE | FETCHCORE_BOUNDED |
|---|---:|---:|---:|
| q-canonical-knowledge | FAIL | FAIL | PASS |
| q-intent-scope | FAIL | FAIL | FAIL |
| q-required-semantics | FAIL | PASS | PASS |
| q-backends | FAIL | FAIL | PASS |
| q-hybrid | FAIL | FAIL | FAIL |
| q-serving | PASS | FAIL | FAIL |

## Failure diagnosis

- `q-canonical-knowledge`: DIRECT and FETCHCORE study pages contained the target
  fact. ai-memory returned a short opening snippet and the evaluator omitted the
  exact canonical/rebuildable wording. Classification: recall snippet + evaluator.
- `q-intent-scope`: all study pages contained the split. BOUNDED's answer was
  semantically correct but used “means” rather than the ruler's `meaning|query|intent`
  and omitted a required SearchScope noun. Classification: evaluator/ruler phrasing.
- `q-required-semantics`: FetchCore arms passed. DIRECT's study memory contained
  `reject` and `unsupported_semantics`, but recall surfaced an earlier nearby
  paragraph and the evaluator returned `UNKNOWN` for the precise behavior.
  Classification: recall snippet + evaluator.
- `q-backends`: DIRECT and FETCHCORE study pages named all four adapters; recalled
  snippets did not surface the full list, and the unbounded evaluator omitted
  Qdrant. Classification: recall snippet + evaluator.
- `q-hybrid`: all study pages explicitly recorded lexical+dense → RRF. Recall
  snippets began before that sentence, so every evaluator omitted RRF.
  Classification: ai-memory recall snippet.
- `q-serving`: all study pages recorded the complete boundary. DIRECT happened
  to receive the exact path in its snippet and passed; both FetchCore evaluators
  paraphrased or saw an incomplete snippet. Classification: recall + evaluator;
  this is the only FetchCore regression relative to DIRECT.

No evidence supports immediate retrieval tuning. The failed questions were
largely acquired and written correctly. The next controlled experiment should
change only memory-page granularity or full-page recall, then rerun before mapping
failures to retrieval backlog items such as #35, #39, #84, #27, or #86.

## Enforcement and limitations

- DIRECT: read-only Codex shell; commands were audited against listing/search/read
  operations; plugins, MCP, browser/computer, and web search were disabled.
- FETCHCORE: Codex shell disabled; only benchmark MCP `search/get_source` exposed.
- FETCHCORE_BOUNDED: the same adapter plus server-side 24-call/20,000-evidence-char
  admission. It completed at 6 calls and 19,076 chars.
- The benchmark MCP adapter delegates to `RetrievalApplicationService` and strips
  large diagnostic/observation envelopes. It is benchmark-only and is not the
  production MCP payload. Retrieval ranking and Ground Truth were not changed.
- The bounded adapter stops admitting evidence once the remaining character
  budget cannot fit another ranked result. Zero-character budget-exhausted replies
  are observable in the tool trace.
- One repository, one scenario, one model, and one run cannot establish generality.
- Regex scoring is deterministic but sensitive to semantically equivalent wording.
- ai-memory `memory_query` returned snippets, not full pages; this was the largest
  observed durable-learning bottleneck.

## Primary commands

```bash
git worktree add --detach \
  /home/operator/projetos/GitHub/fetchcore-rlb-subject \
  e0145894d5b1c0f74c3ba8c9b228556ea7d6d81c

python benchmarks/repository-learning/run.py \
  --scenario benchmarks/repository-learning/scenarios/fetchcore-self.yaml \
  --workspace /home/operator/projetos/GitHub/fetchcore-rlb-subject \
  --output .artifacts/repository-learning/codex-20261008-002/plan \
  --run-id codex-20261008-002 \
  --harness-id codex-cli-0.153.2 \
  --provider-id openai-chatgpt \
  --model-id gpt-5.6-sol \
  --dry-run

AI_MEMORY_DATA_DIR=/tmp/fetchcore-rlb-memory-tc6dti \
  /tmp/fetchcore-rlb-tools/ai-memory serve \
  --transport http --bind 127.0.0.1:49375 --no-watcher

docker exec fetchcore-solr-1 solr create \
  -c fetchcore-rlb-e0145894-20261008-001 -d fetchcore

PYTHONPATH=src python -m fetchcore.cli index \
  --workspace /home/operator/projetos/GitHub/fetchcore-rlb-subject \
  --repository oalangomes/fetchcore --branch master \
  --revision e0145894d5b1c0f74c3ba8c9b228556ea7d6d81c \
  --backend solr \
  --backend-settings '{"url":"http://127.0.0.1:8983/solr","collection":"fetchcore-rlb-e0145894-20261008-001"}' \
  bootstrap

# The same index prefix/settings were used with: dry-run, status, sync, status.

AI_MEMORY_SERVER_URL=http://127.0.0.1:49375 \
python benchmarks/repository-learning/run.py \
  --scenario benchmarks/repository-learning/scenarios/fetchcore-self.yaml \
  --workspace /home/operator/projetos/GitHub/fetchcore-rlb-subject \
  --output .artifacts/repository-learning/codex-20261008-002/run \
  --run-id codex-20261008-002 \
  --harness-id codex-cli-0.153.2 \
  --provider-id openai-chatgpt \
  --model-id gpt-5.6-sol \
  --study-command 'direct=python /home/operator/projetos/GitHub/fetchcore/benchmarks/repository-learning/wrappers/codex_harness.py direct --model gpt-5.6-sol --revision e0145894d5b1c0f74c3ba8c9b228556ea7d6d81c --solr-collection fetchcore-rlb-e0145894-20261008-001' \
  --study-command 'fetchcore=python /home/operator/projetos/GitHub/fetchcore/benchmarks/repository-learning/wrappers/codex_harness.py fetchcore --model gpt-5.6-sol --revision e0145894d5b1c0f74c3ba8c9b228556ea7d6d81c --solr-collection fetchcore-rlb-e0145894-20261008-001' \
  --study-command 'fetchcore_bounded=python /home/operator/projetos/GitHub/fetchcore/benchmarks/repository-learning/wrappers/codex_harness.py fetchcore-bounded --model gpt-5.6-sol --revision e0145894d5b1c0f74c3ba8c9b228556ea7d6d81c --solr-collection fetchcore-rlb-e0145894-20261008-001' \
  --evaluate-command 'python /home/operator/projetos/GitHub/fetchcore/benchmarks/repository-learning/wrappers/codex_harness.py evaluate --model gpt-5.6-sol --revision e0145894d5b1c0f74c3ba8c9b228556ea7d6d81c --solr-collection fetchcore-rlb-e0145894-20261008-001'

.venv/bin/python -m ruff check .
.venv/bin/python -m mypy src
.venv/bin/python -m pytest -q
.venv/bin/python scripts/check_docs.py
.venv/bin/python -m build
```

Validation: Ruff passed; mypy passed for 165 source files; pytest reported
608 passed and 7 skipped; docs passed for 50 Markdown files; sdist and wheel built.
