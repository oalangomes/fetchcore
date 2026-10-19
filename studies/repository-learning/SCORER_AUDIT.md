# Repository Learning Scorer Audit

Status: controlled audit of the canonical `codex-20261008-002-v2` replay. This
experiment changes only the deterministic ruler. It reuses the stored pages,
recalled contexts, and evaluator answers already in the source artifact.

## Method

The audit fixture freezes a semantic verdict for every arm/question pair that
failed at any V2 stage. The harness verifies the source artifact checksum,
repository identity/revision, scenario checksum, and original scorer output. It
then reports true positives, true negatives, false positives, and false negatives
for the original ruler and a separate versioned ruler.

The revised ruler represents required concepts as explicit conjunctions. A
concept may provide reviewed regex alternatives, while exact identifier sets are
represented separately. Forbidden patterns remain fail-closed. No LLM judge is
used, the original scenario regexes are unchanged, and neither FetchCore nor
ai-memory is called.

## Canonical V2 failures

| Arm | Question | Stored semantics | Recalled semantics | Answer semantics | Original ruler | Root cause |
| --- | --- | --- | --- | --- | --- | --- |
| direct | `q-canonical-knowledge` | present | present | correct | FAIL | ruler false negative: `KnowledgeDocument` was identified as canonical and stores as rebuildable projections, without the exact phrase `Canonical Repository Knowledge`. |
| fetchcore | `q-canonical-knowledge` | present | present | correct | FAIL | ruler false negative: “canonical truth is backend-neutral Repository Knowledge” is an equivalent word order. |
| fetchcore_bounded | `q-intent-scope` | present | present | correct | FAIL | ruler false negative: the answer used “means” where the regex allowed `meaning|query|intent`. |
| fetchcore_bounded | `q-serving` | present | present | correct | FAIL | ruler false negative: the application-service/backend-adapter boundary was correct without both exact class names. |

No audited FAIL was an acquisition/learning loss, memory retrieval loss,
evaluator omission, or actually incorrect answer under the frozen semantic audit
verdicts.

## Old versus revised ruler

| Arm | Stage | Original | Ruler v2 |
| --- | --- | ---: | ---: |
| direct | stored / recalled / answer | 5/6 / 5/6 / 5/6 | 6/6 / 6/6 / 6/6 |
| fetchcore | stored / recalled / answer | 6/6 / 6/6 / 5/6 | 6/6 / 6/6 / 6/6 |
| fetchcore_bounded | stored / recalled / answer | 5/6 / 5/6 / 4/6 | 6/6 / 6/6 / 6/6 |

For these frozen outputs, the remaining measured gap was ruler error. This does
not prove the revised ruler is universally semantically complete; its negative
regression cases ensure partial concepts and forbidden claims still fail.

## Reproduction

```bash
python studies/repository-learning/scorer_audit.py \
  --source-run studies/repository-learning/.artifacts/repository-learning/codex-20261008-002-v2/run/run.json \
  --scenario studies/repository-learning/scenarios/fetchcore-self.yaml \
  --ruler studies/repository-learning/scenarios/fetchcore-self-ruler-v2.yaml \
  --annotations studies/repository-learning/scenarios/fetchcore-self-v2-audit.yaml \
  --output /tmp/repository-learning-scorer-audit \
  --run-id codex-20261008-002-v2-scorer-audit
```

The checked-in result is cataloged in the [artifact index](../../studies/repository-learning/.artifacts/repository-learning/INDEX.md).
