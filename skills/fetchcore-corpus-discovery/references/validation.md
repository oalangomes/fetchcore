# Implementation validation and issue acceptance

Validation date: 2026-10-21. Implementation branch: `feat/217-corpus-discovery`.
Baseline: `e0a9a72adb2f8c7e056c5e9993fb5fa34e00e8f1`.
Scope was explicitly authorized by the implementation request; the issue's
earlier design-only gate does not override that authorization.

## Delivered files

- `SKILL.md`: one portable instruction source with DISCOVER/PROPOSE/REVIEW.
- `scripts/inventory.py`: optional deterministic metadata inventory and external
  canonical policy validation/impact review; no apply command or backend access.
- `references/corpus-classification.md` and `policy-contract.md`: evidence
  criteria, current contract and KnowledgePack precedence.
- `references/installation.md`: repository/global/manual installation/invocation.
- `references/fetchcore-example.md` and `.json`: real frozen-revision observations,
  proposed YAML/diff, exact impact paths and risks.
- `references/evaluation-protocol.md`: future controlled A/B protocol.
- `references/validation.md`: this record, including acceptance limitations.
- `tests/skills/test_corpus_discovery.py`: deterministic boundary tests.

No runtime/core/CLI/MCP/indexer/backend/KnowledgePack interface changed. The actual
`.fetchcore/indexing.yaml` remains byte-identical. No PR, merge, issue closure,
reindexing or full retrieval benchmark was performed.

## Deterministic checks

Use the repository's existing development environment:

```bash
python -m ruff check .
python -m mypy src
python -m pytest -q tests/skills/test_corpus_discovery.py
python -m coverage run -m pytest -q
python -m coverage report
python scripts/check_docs.py
python scripts/docs/localize.py check
python -m build --outdir /external/build
```

The 26 skill tests passed. Ruff and mypy passed (181 source files). The skill-creator frontmatter validator
also passed. The ordinary documentation link/index checker passed (74 documents).
The localization checker exited successfully and reported 40 missing, two stale
and two valid projections; this change does not modify translation inputs.

The full initial working-tree suite reported 1163 passed, nine skipped and one
failure: `test_frozen_v02_artifacts_are_present_and_byte_identical` found a
pre-existing, untracked `benchmarks/baseline-v1/artifacts/V0.2/.run-state.json`
(mtime 2026-10-15). That user artifact was preserved. A clean external checkout
with the implementation passed the complete suite: **1164 passed, nine skipped**
before the final four regression tests were added. The final clean-suite result
was **1168 passed, nine skipped in 24.38 seconds**, with **59.99%** runtime
coverage. The Python sdist/wheel build also passed, with output outside the
repository. No tests were weakened/skipped
to accommodate the local artifact. Optional service integrations remain skipped.

The developer helper has no required third-party dependency for inventory;
isolated Python with site-packages disabled verifies the unavailable-validator
path. Canonical proposal validation uses existing installed FetchCore contracts.

## Coder execution evidence

| Coder | Observed version | Execution result | Discovery/behavior evidence |
| --- | --- | --- | --- |
| Codex CLI | 0.163.0 | PASS, read-only sandbox | Loaded `.agents/skills/fetchcore-corpus-discovery` symlink, read SKILL/reference, ran helper, correctly separated tracked ignore match from ignored untracked JSON and retained ambiguous outputs as needs_review |
| GitHub Copilot CLI | 1.0.98 | BLOCKED before analysis | Executable/help checked; smoke returned monthly quota exceeded (exit 1). Successful skill discovery/execution is **not validated** |
| Other coders | not tested | NOT RUN | Full-path manual invocation documented; SKILL.md syntax alone is not a discovery guarantee |

The Codex fixture contained authored Python/docs/tests, existing policy excluding
`private/**`, an output-like tracked JSON, a matching untracked ignored JSON and a
comment-only harness. Codex did not pretend that the comment proved generated
provenance. Its final result kept uncertainty and measured inventory distinct.
SHA-256 snapshots of all fixture files including Git metadata were identical
before and after the successful run. No proposal was applied.

The first Codex smoke exposed a helper dependence on temporary storage inside a
strict read-only sandbox. The final helper streams bounded Git metadata and reads
the current canonical policy directly; DISCOVER creates no temporary files. The
retry succeeded, and a regression test forbids temporary-file creation. REVIEW
still validates external proposals in an external temporary directory and reports
failure if the environment cannot provide one; the manual fallback remains usable.

Smokes were isolated under an explicitly selected external temporary directory,
with responses outside fixture roots. Reproduce with the installed versions:

```bash
codex exec --sandbox read-only --cd /external/fixture \
  'Use $fetchcore-corpus-discovery in DISCOVER mode. No writes, delegation or backend access.'
```

For Copilot, enter the fixture and use a prompt naming the skill, grant only
read/tool-specific inventory permissions, deny write tools and disable built-in
MCPs. Do not use `--allow-all` as a portability check. Retry when account quota
permits; record actual loaded skill path and read-only outcome. The Copilot slash
alias and global installation discovery are documented, not execution-tested.

## Issue #217 acceptance criteria

| Criterion from issue | Evidence / status |
| --- | --- |
| Root invocation, structured path observations, bounded cost | Implemented: SKILL workflow, NUL Git inventory, path cap, 10-second/8-MB command bounds and explicit source-sample budget |
| Default and existing policies; preservation and fail-closed schema | Tested: no policy, existing policy, legacy/conflict, invalid YAML/version/key/enum/limits/extractor; external canonical review |
| Tracked ignored artifacts remain candidates | Tested and observed in Codex smoke; separate ignored-untracked admission |
| Do not discard tests/docs/study source by name; distinguish generated results | Tested against real tracked study paths; sample/producer evidence and narrow JSON-only example |
| SAP conditional defaults and explicit policy precedence | Tested: automatic impex admission/limits, first-policy loss, explicit include restoration, mandatory sensitive excludes |
| Ambiguous/unreadable/sensitive paths marked for review | Tested: unknown extension, unreadable file, sensitive names, symlink targets; no raw secret acquisition |
| Precise diff, rationale/risk, measured vs estimated impact | Real example: 21 exact JSON paths / 1503534 bytes; preserved rules; no invented chunks/quality metrics |
| No workspace/index/backend mutation or execution governance | Snapshot/CLI tests; successful Codex read-only smoke; no indexer/network/backend invocation in helper |
| Portable skill in Codex/Copilot plus manual fallback | Implemented/documented; Codex execution passed. Copilot runtime compatibility remains pending because its account quota blocked the smoke |
| FetchCore artifact fixture separates harness/docs from historical runs | Real tracked study fixture and frozen detached-clone review; JSON-only exclusion preserves Markdown/catalog |
| Controlled before/after evaluation protocol | Documented in evaluation-protocol.md: same revision/backend/lexical profile/questions/GT, one selection variable and required coverage/density/volume/latency/cost metrics |

The Copilot runtime validation limitation remains explicit; no blanket claim that
every acceptance criterion has been independently execution-proven is made.
