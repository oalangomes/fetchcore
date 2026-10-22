# Frozen comparative assessment plan (set before critic response)
Date: 2026-10-21. Product: FetchCore corpus advisor skill. Baseline A: the existing one-invocation exercise in skills/fetchcore-corpus-advisor/references/validation.md (recorded before this change). Experiment B: same two Git revisions and canonical CLI, one native independent critic, parent reconciliation, external proposal validation. No policy/index writes.
Repositories: oalangomes/RunnerOps@5a72fa122d5664e217299e16420fcd7d48b67a92; oalangomes/fetchcore@600651415055e625b2d98c57321263a68846455d. CLI: /home/alangomes/projetos/GitHub/fetchcore/.venv/bin/fetchcore. Raw B observations: /tmp/fetchcore-eval-{runnerops,fetchcore}-{scan,deep}.json. Baseline observations and decisions: pre-existing validation.md.

Criteria, fixed before review:
1. Supported inclusion requires a concrete engineering question answered by knowledge that selected sources do not adequately supply, traceable source lines, and no unmitigated sensitive-data concern. A consumer or extension alone does not suffice.
2. Supported exclusion requires a concrete retrieval-noise or safety reason and evidence that loss of unique technical knowledge is acceptable. Keep-for-reproduction and index-for-retrieval are distinct.
3. False-positive inclusion: baseline proposes admission but independent evidence supports rejection or needs_evidence. Count only if parent accepts the objection; ambiguous cases remain unresolved, not false positives.
4. Important gap: source with demonstrated unique public behavior or technical contract omitted from current policy and baseline proposal. Count separately from proposed changes.
5. Objection quality: path/line-supported, specific to marginal retrieval value, and checked against selected alternatives. Generic caution or agent agreement does not count.
6. Decision correction: final B differs from A for a candidate with a documented evidence path. Record the direction and rationale.
7. Cost: count canonical calls, focused searches and source reads where available; report elapsed time/tokens only if instrumented. Do not invent missing telemetry.
8. Ambiguous: if source value or risk cannot be settled by bounded evidence, mark needs_review; human inspection remains required before adoption. No model output is Ground Truth.

Case prompts: imported CSS; extensionless public CLI; duplicate benchmark queries; example env; image lock; nginx and runner-control configuration. These are criteria probes, not rules by filename.
