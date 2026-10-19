# Repository Learning V1 to V2 Controlled Conclusion

Status: frozen interpretation of `codex-20261008-002` and its canonical
`codex-20261008-002-v2` controlled replay. The [artifact index](../../benchmarks/repository-learning/.artifacts/repository-learning/INDEX.md)
is the source of truth for run classification.

## Hypothesis and controlled variable

The V1 score could have been limited by the memory recall and evaluation pipeline,
not necessarily by FetchCore acquisition quality. The controlled V2 replay tests
that hypothesis by changing only the recall path:

```text
V1: memory_query snippet → evaluator
V2: memory_query → ranked page paths → memory_read_page → evaluator
```

V2 reuses the exact V1 study pages and acquisition telemetry. The subject
repository revision, scenario, Ground Truth, original regex scorer, source-run
model/provider/harness, and acquisition data remain fixed. V2 therefore does not
constitute a fresh acquisition run.

## Result

| Arm | StoredFactRecall | MemoryRecallCoverage | AnswerFactRecall | Returned repository chars | Input tokens | Acquisition tool calls |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| direct | 5/6 | 5/6 | 5/6 | 1,079,418 | 71,523 | 2 |
| fetchcore | 6/6 | 6/6 | 5/6 | 108,799 | 214,894 | 17 |
| fetchcore_bounded | 5/6 | 5/6 | 4/6 | 19,076 | 52,414 | 6 |

The bounded arm returned about 56 times fewer raw repository characters than the
direct arm. That evidence compression is not an equivalent token saving: its
input-token count was only 26.7% lower (52,414 versus 71,523), because model/tool
iteration overhead remained material. Neither ratio is a general quality
multiplier.

## Interpretation

V1's 1/6 final score cannot be attributed directly to retrieval quality. Correct
facts already existed in the durable study pages, and full-page expansion removed
most of the snippet-recall loss. In the unbounded FetchCore arm, all six facts were
present both in stored memory and recalled context, while the original evaluator
answer/ruler stage retained five.

The remaining gap is downstream of repository acquisition in that arm. It must be
audited across evaluator output and deterministic ruler behavior before any
FetchCore retrieval tuning. Regex matches are measurement observations, not
semantic authority.

The controlled evidence permits this claim:

> In this controlled scenario, FetchCore-mediated acquisition allowed the agent
> to reconstruct comparable or greater knowledge coverage than direct exploration
> while returning a fraction of the raw repository evidence.

It does not permit claims of "56x better", "6x more learning", universal
superiority, ai-memory failure, proven V1 retrieval weakness, or semantic
authority for AnswerFactRecall.

