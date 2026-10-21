# Evidence-led corpus classification

Use path/extension heuristics to choose samples, never to decide semantic value.
The helper's classification is deliberately `needs_review` with low confidence:
its deterministic labels describe path observations, not LLM judgments.

| Candidate | Starting recommendation | Evidence to inspect |
| --- | --- | --- |
| Implementation and interfaces | Prioritize | declarations, public APIs, callers, manifest |
| Architecture, ADRs, contracts, schemas | Prioritize | headings, schema declarations, cross-references |
| Configuration, build, infrastructure | Generally retain | manifests, workflow/deployment definitions |
| Tests and fixtures | Retain when technical | behavior asserted, realistic contracts; sensitive fixtures metadata only |
| Examples | Evaluate | runnable usage, maintained API examples |
| Benchmark/study docs and harness | Evaluate separately | protocol, experiment decisions, scorer/runner source |
| Historical evaluation results | Candidate exclusion | producer write site + sampled run metadata + exact paths |
| Generated SDK/schema/code | Evaluate | absence of equivalent authored source, consumer usage |
| Logs, caches, traces, backups | Candidate exclusion | output producer, repeatability, technical uniqueness |
| Duplicates/mirrors | Investigate | measured checksum equivalence; filename/size alone is insufficient |
| Sensitive/unreadable/ambiguous | needs_review | metadata only; never read to prove sensitivity |

Distinguish authored READMEs or experiment notes within an artifact tree from its
machine-emitted payloads. A broad exclusion can discard useful reproducibility
evidence. Use producer statements with source spans and bounded samples to justify
generated classification; preserve uncertain files pending review.

## Manual inventory fallback

When the helper cannot run, enumerate with NUL-delimited Git output and parse paths
without shell word splitting (filenames may contain spaces/newlines):

```bash
git --no-optional-locks ls-files -z --cached
git --no-optional-locks ls-files -z --others --exclude-standard
git --no-optional-locks ls-files -z --others --ignored --exclude-standard --directory
```

Batch tracked paths through `git check-ignore --no-index -z --stdin` to identify
tracked paths matching ignore rules. Such files remain candidates: ignore rules
do not untrack them. Ignored untracked directories are collapsed; report their
unknown descendants and bytes. Never traverse dependencies/caches just to count
them. Without Git, use a bounded metadata traversal and report tracking/ignore
facts as unknown. A failed Git inventory is a failure, not an empty repository.

The helper also observes `git --no-optional-locks status --porcelain=v1 -z`
without content diffs. It preserves index/workspace status columns, additions,
deletions and rename source/destination paths, including whitespace/newlines.
`revision` always refers to committed HEAD. `working_tree_dirty` is null when
status is unavailable, not false; unborn HEAD cannot represent the workspace.
Status records do not fingerprint already-dirty file contents and are observed
separately from file metadata, not as an atomic snapshot.

Record at most 10000 paths by default. The helper time-bounds each Git command to
10 seconds and rejects metadata output larger than 8 MB. Git may enumerate more
paths before truncation, but the helper bounds the metadata pipe; this is
not a hard time budget for the entire repository. Plain directory traversal
prunes dependency directories and caps directory visits. Report limitations.

`observation` declares completeness, scope and reason codes. A path budget,
collapsed ignored directory, pruned/unreadable directory or unresolved source
metadata prevents a complete observation. `enumeration_complete` is separate
from metadata availability; source contents are never sampled by this helper.
`unvisited_entries` counts known enumerated entries omitted by a Git path budget;
it does not count unknown descendants of collapsed directories. Non-Git traversal
stops/pruning use null for that count. Never turn unknown descendants into zero
files/bytes. REVIEW copies these limitations into `impact.complete/scope/reasons`;
all distributions remain measurements over observed paths, before indexing.

Before sampling check all parent components for symlinks and check filename-based
sensitivity (including `.env*`, credential names, keys and `local.properties`).
Do not sample excluded dependency/output trees without a specific technical
question. Do not dump raw manifests that may contain credentials. A negative
filename check cannot prove content is safe; inspect only necessary structure
and stop/redact if sensitive values appear. No report should quote secrets.

No recognized framework is required. Unknown extensions may be important technical
sources; classify as `needs_review`, suggest a bounded sample or explicit include
only after evidence. Current FetchCore coverage may differ from potential value.
