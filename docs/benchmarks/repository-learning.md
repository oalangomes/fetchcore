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
fresh evaluator
      ↓
deterministic fact score
```

The v1 arms are direct repository exploration, FetchCore-mediated acquisition and bounded FetchCore-mediated acquisition. They share the same study objective and evaluation ruler. Each arm receives a unique memory project on a fresh/disposable ai-memory server so unrelated or cross-arm memory cannot contaminate the comparison.

The benchmark deliberately keeps these concepts separate:

```text
retrieval quality != durable learning quality != authority
```

FetchCore owns evidence acquisition. The external harness owns agent/tool behavior. `ai-memory` owns durable recall. The versioned benchmark scorer owns only the experimental fact rubric.

## Metrics

The harness records acquisition cost when the external wrapper can report it: tool calls, files read, evidence items, returned characters and model token counts. The fresh-memory evaluation records FactRecall, FactPrecision, unsupported/incorrect answers, memory pages written, recall payloads and elapsed time.

A wrapper that cannot verify actual tool events is marked externally unverified. Prompt instructions alone are not proof that the direct arm avoided FetchCore or that FetchCore arms avoided raw repository reads.

## Protocol and reproduction

The executable protocol, prompts, self-study scenario and artifact contract live in [`benchmarks/repository-learning/`](../../benchmarks/repository-learning/). Its README documents dry-run validation and the opt-in end-to-end setup with an external agent wrapper plus a fresh ai-memory MCP server.

The initial scenario is intentionally small and must not become a tuning corpus. Results are product evidence for the measured repository, revision, model and harness—not a universal superiority claim.
