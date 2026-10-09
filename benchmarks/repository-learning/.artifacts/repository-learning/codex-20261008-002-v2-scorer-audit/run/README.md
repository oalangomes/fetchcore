# Repository Learning Scorer Audit

- Protocol: `fetchcore.repository-learning-scorer-audit-v1`
- Source run: `codex-20261008-002-v2`
- Original scorer: `scenario-required-regex-v1`
- Revised scorer: `fetchcore.repository-learning-ruler-v2`

| Arm | Question | Stored | Recalled | Answer | Original ruler | Root cause |
| --- | --- | --- | --- | --- | --- | --- |
| direct | q-canonical-knowledge | present | present | present | FAIL | ruler false negative |
| fetchcore | q-canonical-knowledge | present | present | present | FAIL | ruler false negative |
| fetchcore_bounded | q-intent-scope | present | present | present | FAIL | ruler false negative |
| fetchcore_bounded | q-serving | present | present | present | FAIL | ruler false negative |

Semantic verdicts are frozen human-reviewed audit inputs. The harness
deterministically verifies the source artifact, reproduces the original
regex results, classifies confusion outcomes, and applies the separate
versioned ruler. It does not use an LLM judge and does not rewrite history.
