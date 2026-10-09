# Repository Learning Benchmark

Status: experimental product-proof harness owned by #89 / #33. It is not a FetchCore runtime contract.

The benchmark asks:

> When a fresh agent must learn a repository, does FetchCore-mediated evidence produce durable technical memory with less acquisition/context cost and equal or better factual recall than direct repository exploration?

Protocol v2 separates four observable stages:

```text
repository acquisition → durable memory construction → ai-memory recall → fresh answer
       FetchCore?              stored pages          query + read_page    evaluator
```

The v1 artifacts remain checked in as historical evidence. V2 changes the recall
protocol, not FetchCore retrieval: `memory_query` discovers relevant pages and
`memory_read_page` expands the top ranked pages before evaluation.

**retrieval != learning != authority**

## Arms

The protocol runs the same study objective and evaluation questions through:

- `direct`: ordinary repository exploration, no FetchCore retrieval;
- `fetchcore`: FetchCore `search/get_source` acquisition;
- `fetchcore_bounded`: the same FetchCore path with explicit tool/context budgets.

The external agent wrapper owns tool exposure. Keep model, harness, provider, repository revision and evaluator fixed where practical. If the wrapper cannot provide observed tool events, the artifact records `tool_events_verified=false`; prompt instructions are not treated as proof of policy enforcement.

## ai-memory boundary

`akitaonrails/ai-memory` is an **external** durable-memory system. The study process emits Markdown pages; the runner persists them through ai-memory's public MCP `memory_write_page` tool. For each evaluation question the runner calls `memory_query` for discovery, then reads up to three ranked unique pages in full through `memory_read_page` by exact path. A fresh evaluator receives only that isolated memory-derived context.

The default expansion limit is configurable with `--memory-read-page-limit`.
It is recorded in the run artifact together with the query limit so memory-context
cost remains observable rather than hidden.

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

## Controlled v1 → v2 re-evaluation

To isolate the recall fix from study-agent variability, V2 can reuse the durable
pages already produced by a completed run. This path does **not** call DIRECT or
FetchCore acquisition again. It writes the recorded pages into fresh ai-memory
projects, performs `memory_query → memory_read_page`, and starts only the fresh
evaluator:

```bash
python benchmarks/repository-learning/reevaluate.py \
  --source-run benchmarks/repository-learning/.artifacts/repository-learning/codex-20261008-002/run/run.json \
  --scenario benchmarks/repository-learning/scenarios/fetchcore-self.yaml \
  --output /tmp/fetchcore-rlb-v2-reeval \
  --run-id codex-20261008-002-v2
```

Use a fresh/disposable ai-memory server for this command too. The output records
the source run/protocol and marks every arm with `study_reused=true`. This is
the preferred first V2 measurement because the intentional changed variable is
only durable-memory recall.

For Codex source runs, the replay inherits harness id, provider, model and
repository revision from the source artifact and builds the bundled evaluator
command with an absolute wrapper path before entering the evaluator's temporary
working directory. Explicit `--harness-id`, `--provider-id` or `--model-id`
values are accepted only when they match the source run; mismatches fail closed.
A custom/non-Codex source harness must provide `--evaluate-command`.

## Opt-in end-to-end run

Start ai-memory with a **fresh data directory** and its stateless Streamable HTTP MCP endpoint.

For the Docker wrapper installation, start the persistent benchmark server
directly so port 49374 is published to the host:

```bash
export AI_MEMORY_DATA_DIR="$(mktemp -d)"
docker rm -f ai-memory-rlb 2>/dev/null || true
docker run -d \
  --name ai-memory-rlb \
  --rm \
  -p 127.0.0.1:49374:49374 \
  -v "$AI_MEMORY_DATA_DIR:/data" \
  docker.io/akitaonrails/ai-memory:latest
```

A native ai-memory binary can instead run `serve --transport http` directly.
Do not use the Docker CLI wrapper itself as the long-lived server process: its
loopback listener belongs to the helper container unless the port is explicitly
published.

In the shell running the benchmark:

```bash
export AI_MEMORY_SERVER_URL=http://127.0.0.1:49374
export AI_MEMORY_AUTH_TOKEN=...   # omit only for intentional unauthenticated loopback
```

Do not add `--http-stateful` for this runner: its minimal MCP client intentionally uses stateless requests.

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

The checked-in [artifact index](.artifacts/repository-learning/INDEX.md) is the
authoritative catalog for canonical, reproduction, and invalid runs. Preserve
original run IDs and source-run paths when adding evidence.

The ruler uses the same hidden question-level required/forbidden regexes at three
distinct stages:

- **StoredFactRecall**: facts already present in the durable pages emitted by the study agent;
- **MemoryRecallCoverage**: facts present in the `memory_query → memory_read_page` context;
- **AnswerFactRecall / FactPrecision**: facts preserved in the fresh evaluator answer;
- per-question PASS/FAIL remains available at every stage.

This makes loss attribution explicit: acquisition/learning, memory retrieval and
fresh answering are no longer collapsed into one number. The deterministic ruler
is still intentionally lexical and can have phrasing false negatives; that is a
separate controlled variable from the v2 recall change.

`scorer_audit.py` evaluates that variable without rerunning acquisition, memory
recall, or the evaluator. It verifies the frozen source artifact, reproduces the
original stage scores, and applies an explicitly versioned concept ruler. See the
[scorer audit](../../docs/benchmarks/repository-learning-scorer-audit.md) for the
canonical result and reproduction command.

This is a deterministic **learning-memory** ruler, not a FetchCore retrieval metric and not an authority score. Retrievalgate can evaluate retrieval-level regressions separately.

## Initial scenario

`scenarios/fetchcore-self.yaml` freezes a FetchCore revision and asks the study agent to learn six architectural facts: canonical Repository Knowledge, SearchIntent/SearchScope separation, fail-closed required semantics, current retrieval adapters, RRF hybrid baseline and the MCP/application/backend serving boundary.

The scenario is deliberately small. It proves the harness before adding RunnerOps/retrievalgate cases. Do not tune FetchCore against these questions.

A valid result can support a bounded claim such as:

> On these scenarios and this fixed harness/model, FetchCore-mediated acquisition recovered more durable facts and/or used less repository context than direct exploration.

It cannot establish universal agent superiority.
