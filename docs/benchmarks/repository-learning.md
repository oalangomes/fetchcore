# Repository Learning Benchmark

Status: experimental study under #33/#89. This is not part of FetchCore production retrieval semantics.

The Repository Learning Benchmark tests FetchCore as an external dependency of an agent-learning workflow. A study agent learns a frozen repository through one acquisition arm, produces durable Markdown memory, the runner persists that memory through `ai-memory`, and a fresh process answers evaluation questions from memory recall only.

```text
frozen repository
      ↓
acquisition arm
      ↓
study agent
      ↓
ai-memory pages
      ↓
memory_query
      ↓
memory_read_page
      ↓
fresh evaluator
      ↓
deterministic staged scores
```

The arms are direct repository exploration, FetchCore-mediated acquisition and bounded FetchCore-mediated acquisition. They share the same study objective and evaluation ruler. Each arm receives a unique memory project on a fresh/disposable ai-memory server so unrelated or cross-arm memory cannot contaminate the comparison.

Protocol v2 keeps repository acquisition unchanged and corrects the durable-memory
read path: `memory_query` is discovery, not the final context. The runner expands
ranked hits through exact-path `memory_read_page` calls before the fresh evaluator
runs. Historical v1 artifacts remain available for comparison.

The benchmark deliberately keeps these concepts separate:

```text
retrieval quality != durable learning quality != authority
```

FetchCore owns evidence acquisition. The external harness owns agent/tool behavior. `ai-memory` owns durable recall. The versioned benchmark scorer owns only the experimental fact rubric.

## Metrics

The harness records acquisition cost when the external wrapper can report it: tool calls, files read, evidence items, returned characters and model token counts. V2 then reports three deterministic knowledge stages: StoredFactRecall over the durable pages, MemoryRecallCoverage over the expanded ai-memory context, and AnswerFactRecall/FactPrecision over the fresh evaluator output. It also records memory queries, full-page reads, distinct pages read, memory-context characters and elapsed time.

A wrapper that cannot verify actual tool events is marked externally unverified. Prompt instructions alone are not proof that the direct arm avoided FetchCore or that FetchCore arms avoided raw repository reads.

## Protocol and reproduction

The executable protocol, prompts, self-study scenario and artifact contract live in [`benchmarks/repository-learning/`](../../benchmarks/repository-learning/). Its README documents dry-run validation and the opt-in end-to-end setup with an external agent wrapper plus a fresh ai-memory MCP server.

The initial scenario is intentionally small and must not become a tuning corpus. Results are product evidence for the measured repository, revision, model and harness—not a universal superiority claim.


## V1 to V2 interpretation

V1 supplied short `memory_query` snippets directly to the evaluator. The first
real run showed that study pages frequently contained the expected facts even
when the final answer failed because the snippet ended before the relevant
sentence. V2 treats that as a benchmark-pipeline loss instead of a FetchCore
retrieval loss.

The controlled V2 experiment should therefore keep the frozen repository,
scenario, FetchCore index/retrieval configuration, model, study prompt and
acquisition budgets fixed. The intentional changed variable is the ai-memory
recall path:

```text
v1: memory_query → evaluator
v2: memory_query → memory_read_page(top ranked pages) → evaluator
```

Scorer/rubric changes and memory-page-granularity changes remain separate future
experiments so a better score cannot be attributed to multiple simultaneous
variables.
