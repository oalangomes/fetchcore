# Repository Learning Artifact Index

This is the authoritative catalog for checked-in Repository Learning Benchmark
evidence. Artifact directories retain their original paths because source-run
references and reproduction commands already identify them there. Run IDs inside
JSON artifacts remain unchanged.

## Official evidence

| Run ID | Protocol / mode | Status | Classification | Source run | Subject revision | Harness / model | ai-memory | Result or limitation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `codex-20261008-001` | `fetchcore.repository-learning-v1` / fresh plan | incomplete | invalid historical attempt | none | `e0145894d5b1c0f74c3ba8c9b228556ea7d6d81c` | not recorded | not recorded | Contains a plan plus partial `direct` and `fetchcore` arm files. It has no bounded arm, aggregate `run.json`, or completed-run identity. It cannot support comparative or score claims. |
| `codex-20261008-002` | `fetchcore.repository-learning-v1` / fresh acquisition | complete | **canonical V1 baseline** | none | `e0145894d5b1c0f74c3ba8c9b228556ea7d6d81c` | `codex-cli-0.153.2` / `openai-chatgpt` / `gpt-5.6-sol` | `2.6.2` | Supports the frozen V1 acquisition-cost record and the historical snippet-only end-to-end result. It does not isolate retrieval quality from memory recall, evaluator, or regex-ruler loss. |
| `codex-20261008-002-v2` | `fetchcore.repository-learning-v2` / `reevaluation` | complete | **canonical V2 controlled replay** | `codex-20261008-002` | `e0145894d5b1c0f74c3ba8c9b228556ea7d6d81c` | inherited `codex-cli-0.153.2` / `openai-chatgpt` / `gpt-5.6-sol` | `2.6.2` | Official V2 result to cite: `direct` 5/6 stored, recalled, and answered; `fetchcore` 6/6 stored, 6/6 recalled, 5/6 answered; bounded 5/6 stored, 5/6 recalled, 4/6 answered under the original ruler. It isolates full-page recall from acquisition because it reuses V1 study pages. It does not establish fresh-run reproducibility, universal superiority, or semantic authority. |
| `codex-20261008-002-v2-repro` | `fetchcore.repository-learning-v2` / `reevaluation` | complete | reproduction | `codex-20261008-002` | `e0145894d5b1c0f74c3ba8c9b228556ea7d6d81c` | inherited `codex-cli-0.153.2` / `openai-chatgpt` / `gpt-5.6-sol` | `2.6.2` | Reproduces stored and memory-recall stages. Its fresh evaluator produced bounded 5/6 rather than the canonical replay's 4/6, demonstrating evaluator-output variance. It is corroborating evidence, not the baseline replacement. |
| `codex-20261008-002-v2-scorer-audit` | `fetchcore.repository-learning-scorer-audit-v1` / `scorer_audit` | complete | canonical scorer audit | `codex-20261008-002-v2` | inherited `e0145894d5b1c0f74c3ba8c9b228556ea7d6d81c` | no model execution | not invoked | Re-scores the exact canonical V2 texts with the original ruler and separate `fetchcore.repository-learning-ruler-v2`. Human-reviewed semantic verdicts classify all four original failing arm/question pairs as ruler false negatives; the revised deterministic ruler records 6/6 at every stage/arm. It supports a measurement-error claim only for these frozen outputs, not a new acquisition or general quality claim. |
| `codex-20261009-003-v2-fresh` | `fetchcore.repository-learning-v2` / full fresh | complete | **canonical full fresh V2** | none | `e0145894d5b1c0f74c3ba8c9b228556ea7d6d81c` | `codex-cli-0.153.2` / `openai-chatgpt` / `gpt-5.6-sol` | `2.6.2` | Fresh agent acquisition, memory, full-page recall, and evaluator execution. Original-ruler results: direct 6/6 stored/recalled/answer; FetchCore 6/6 stored/recalled and 5/6 answer; bounded 5/6 stored/recalled and 4/6 answer. The FetchCore arms reproduce the controlled replay scores. It does not isolate a single variable from the replay and does not establish generality. |

The source-run harness revision recorded by the canonical artifacts is
`962839e6613458461b45576ddc5ed9ca41a569d7`. The frozen scenario SHA-256 is
`2753adf17add395ae4977424c537f6aab13449bcc05327beb5130b60b319c302`.

No checked-in run is currently classified as `failed` or `scratch`. An absent
category is not evidence that no external or uncommitted attempts existed.

## Citation policy

Use `codex-20261008-002` for V1 acquisition metrics and historical V1 outcomes.
Use `codex-20261008-002-v2` for the controlled V1-to-V2 recall comparison. Use
`codex-20261008-002-v2-repro` only when discussing reproduction or evaluator
variance. Use `codex-20261008-002-v2-scorer-audit` only for the separate
old-ruler-versus-ruler-v2 measurement experiment. Use
`codex-20261009-003-v2-fresh` for fresh-run V2 results, not for the controlled
V1-to-V2 causal comparison.

These artifacts may support this bounded statement:

> In this controlled scenario, FetchCore-mediated acquisition allowed the agent
> to reconstruct comparable or greater knowledge coverage than direct exploration
> while returning a fraction of the raw repository evidence.

They do not support claims that FetchCore is "56x better", learns "6x more", is
universally superior, that ai-memory failed, that V1 proved poor retrieval, or
that AnswerFactRecall is semantic authority.

