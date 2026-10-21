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
`.fetchcore/indexing.yaml` remains byte-identical. The initial checkpoint below
preceded PR #219. No merge, issue closure, reindexing or full retrieval benchmark
was performed.

## PR #219 review remediation

The existing skill was corrected in place from `5d056a6`, without changing
runtime contracts or agent integrations. Its developer-report JSON deliberately
adds visibility, Git-state and completeness fields. `policy.yaml` can now be null
when invalid, unvalidated or potentially sensitive; consumers must inspect its
explicit `yaml_status` rather than treating withheld text as an empty policy.

| Finding | Correction | Regression evidence | Status |
| --- | --- | --- | --- |
| P1: sensitive invalid YAML | Keep validation inputs private; withhold invalid/unvalidated text and suspicious valid text, resolved values and diffs; retain path/hash and safe errors | Invalid syntax/keys/values, comments, current/proposed/both policies, missing validator, safe unchanged valid YAML, real CLI stdout/stderr | Fixed |
| P2: dirty working tree | NUL-delimited Git porcelain with separate staged/unstaged status and rename origins; HEAD remains a committed revision, unavailable status is unknown | Clean, same-size edit, staged, unstaged, combined, added/deleted/renamed unusual paths, unborn HEAD and status failure; index/files unchanged | Fixed |
| P2: partial impact | Structured observation/impact completeness, scope and reasons; distinguish known omitted entries from unknown descendants and metadata issues | Complete/limited Git and plain directories, ignored/pruned directories, unresolved metadata, complete/partial REVIEW | Fixed |

Remediation validation: **49 skill tests passed**, followed by Ruff on changed
Python files (including executable checks), mypy over 181 runtime source files,
and **568 contract/repository boundary tests passed**. The complete suite in an
external clean checkout with this patch passed: **1191 passed, nine skipped in
35.13 seconds**, **59.99%** runtime coverage. The original untracked V0.2
`.run-state.json` was preserved. Documentation checks and the sdist/wheel build
passed. Localization still reports the pre-existing 40 missing/two stale/two valid
projections without a failing exit code. The real dirty-workspace/invalid-policy
CLI regression exits 2, emits no sentinel value and leaves all files/Git metadata
byte-identical. Existing legacy-policy, ignore, KnowledgePack and study tests pass.

The frozen worked example was regenerated with structured observations; its
affected paths, counts and bytes are unchanged. No selection/ranking/indexing
algorithm changed and no new retrieval benchmark was run.

The previous self-dogfood failure was investigated separately:
[run 39605872713](https://github.com/oalangomes/fetchcore/actions/runs/39605872713),
job `self-index / index`, attempt 1, failed at `Discover and prepare Solr resource`.
At 2026-10-21 13:12:32 UTC the runner's preflight reported HTTP 503,
`stage=solr_preflight`, `category=service_error`; schema bootstrap, indexing and
evaluation were skipped. This is evidence of service unavailability at that
preflight, not a helper failure. The endpoint was runner-local and the cleanup
step completed; this workspace cannot probe that historical ephemeral instance
or recover its internal service state from the available diagnostic. Its deeper
cause remains unconfirmed. No backend, workflow, readiness check or fallback was
modified to hide the failure. New checks must be assessed separately after push.

Residual limits: the presentation guard is deliberately conservative, may hide
benign policy text and is not a complete secret scanner. Git status does not hash
already-dirty contents for report identity; metadata/status observations are not
atomic. Metadata completeness never means full content inspection or actual
indexing. Copilot execution remains unvalidated because of the earlier account
quota; installation/invocation instructions are unchanged.

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
