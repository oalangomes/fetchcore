# Repository Learning Benchmark

Status: experimental product-proof harness owned by #89 / #33. It is not a FetchCore runtime contract.

The benchmark asks:

> When a fresh agent must learn a repository, does FetchCore-mediated evidence produce durable technical memory with less acquisition/context cost and equal or better factual recall than direct repository exploration?

It separates three layers:

```text
repository acquisition → durable memory construction → fresh-session recall
       FetchCore?              ai-memory               deterministic score
```

**retrieval != learning != authority**

## Arms

The v1 protocol runs the same study objective and evaluation questions through:

- `direct`: ordinary repository exploration, no FetchCore retrieval;
- `fetchcore`: FetchCore `search/get_source` acquisition;
- `fetchcore_bounded`: the same FetchCore path with explicit tool/context budgets.

The external agent wrapper owns tool exposure. Keep model, harness, provider, repository revision and evaluator fixed where practical. If the wrapper cannot provide observed tool events, the artifact records `tool_events_verified=false`; prompt instructions are not treated as proof of policy enforcement.

## ai-memory boundary

`akitaonrails/ai-memory` is an **external** durable-memory system. The study process emits Markdown pages; the runner persists them through ai-memory's public MCP `memory_write_page` tool. For each evaluation question the runner calls `memory_query`; a fresh evaluator receives only those recall payloads.

FetchCore never imports ai-memory and owns no memory/lifecycle semantics.

Use a **fresh/disposable ai-memory server/data directory for each benchmark run**. Normal ai-memory project recall can include reserved global/profile material, so a fresh server prevents unrelated memory from contaminating comparisons. Each arm also gets a unique project:

```text
fetchcore-rlb / rlb-<run>-<scenario>-direct
fetchcore-rlb / rlb-<run>-<scenario>-fetchcore
fetchcore-rlb / rlb-<run>-<scenario>-fetchcore-bounded
```

## External process contract

Study and evaluator commands:

1. read the complete prompt from stdin;
2. run one fresh agent/session;
3. emit exactly one JSON object on stdout;
4. send logs only to stderr.

Study output:

```json
{
  "status": "success",
  "memory_pages": [
    {"path": "knowledge/retrieval.md", "body": "# Retrieval\n\nDurable facts..."}
  ],
  "metrics": {
    "tool_calls": 12,
    "files_read": 4,
    "evidence_items": 18,
    "returned_chars": 12000,
    "input_tokens": 9000,
    "output_tokens": 1800
  },
  "tool_events_verified": true,
  "tool_events": [{"tool": "fetchcore.search"}]
}
```

Evaluator output:

```json
{
  "answers": [
    {"question_id": "q-canonical-knowledge", "answer": "memory-grounded answer"}
  ]
}
```

Malformed, incomplete or unknown IDs fail closed. The bounded arm also fails when reported acquisition metrics exceed its scenario budget.

## Dry-run validation

No LLM or ai-memory server is needed to validate the scenario, freeze and prompt rendering:

```bash
python benchmarks/repository-learning/run.py \
  --scenario benchmarks/repository-learning/scenarios/fetchcore-self.yaml \
  --workspace /path/to/fetchcore-at-frozen-revision \
  --output /tmp/fetchcore-rlb-plan \
  --run-id smoke-001 \
  --harness-id codex \
  --provider-id openai \
  --model-id example \
  --dry-run
```

The workspace must be clean and exactly match the scenario revision.

## Opt-in end-to-end run

Start ai-memory with a **fresh data directory**, expose its Streamable HTTP MCP endpoint, and set:

```bash
export AI_MEMORY_SERVER_URL=http://127.0.0.1:PORT
export AI_MEMORY_AUTH_TOKEN=...   # omit only for intentional unauthenticated loopback
```

Then provide wrapper commands for all study arms plus one fresh evaluator command:

```bash
python benchmarks/repository-learning/run.py \
  --scenario benchmarks/repository-learning/scenarios/fetchcore-self.yaml \
  --workspace /path/to/fetchcore-at-frozen-revision \
  --output /tmp/fetchcore-rlb-run \
  --run-id run-001 \
  --harness-id <harness> \
  --provider-id <provider> \
  --model-id <model> \
  --study-command 'direct=/path/to/direct-wrapper' \
  --study-command 'fetchcore=/path/to/fetchcore-wrapper' \
  --study-command 'fetchcore_bounded=/path/to/fetchcore-bounded-wrapper' \
  --evaluate-command '/path/to/fresh-evaluator-wrapper'
```

The direct wrapper must expose only ordinary repository exploration. FetchCore wrappers must expose only the intended FetchCore acquisition surface. The evaluator runs in a fresh temporary directory and is never given the repository path.

## Artifacts and scoring

A completed run writes per-arm JSON plus `run.json` and a generated `README.md`. Artifacts record frozen repository identity, scenario/prompt fingerprints, FetchCore revision, harness/provider/model identity, ai-memory server identity when advertised, unique memory scope, acquisition metrics, recall payloads, answers and deterministic scores.

The ruler uses question-level required/forbidden regexes hidden from the evaluator:

- **FactRecall**: fraction of declared facts recovered by at least one passing question;
- **FactPrecision**: passing answers / non-empty answers;
- unsupported/incorrect answer count;
- per-question required-pattern misses and forbidden-pattern hits.

This is a deterministic **learning-memory** ruler, not a FetchCore retrieval metric and not an authority score. Retrievalgate can evaluate retrieval-level regressions separately.

## Initial scenario

`scenarios/fetchcore-self.yaml` freezes a FetchCore revision and asks the study agent to learn six architectural facts: canonical Repository Knowledge, SearchIntent/SearchScope separation, fail-closed required semantics, current retrieval adapters, RRF hybrid baseline and the MCP/application/backend serving boundary.

The scenario is deliberately small. It proves the harness before adding RunnerOps/retrievalgate cases. Do not tune FetchCore against these questions.

A valid result can support a bounded claim such as:

> On these scenarios and this fixed harness/model, FetchCore-mediated acquisition recovered more durable facts and/or used less repository context than direct exploration.

It cannot establish universal agent superiority.
