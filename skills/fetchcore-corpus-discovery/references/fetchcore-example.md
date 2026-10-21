# FetchCore worked example

Status: advisory proposal, **not applied**. Observed repository:
`oalangomes/fetchcore@e0a9a72adb2f8c7e056c5e9993fb5fa34e00e8f1`.
The inventory ran on a clean, detached local clone of that revision, outside the
implementation workspace. No service or index was contacted. The clone stayed
clean after DISCOVER/REVIEW. The original operational policy is unchanged.

## DISCOVER observations

The [structured report](fetchcore-example.json) records the source-kind
distribution, policy hashes, evidence, exact affected paths, proposal and diff.
Git listed 715 tracked regular files, 139754846 bytes. Metadata admission was:

| Observed state | Files | Bytes |
| --- | ---: | ---: |
| Eligible by current selection and per-file size | 585 | 6682736 |
| Excluded by current/default policy | 60 | 114342538 |
| Not matched by includes | 70 | 18729572 |

No ignored untracked files existed in this clean clone. A separate fixture proves
that already-tracked ignore matches stay eligible and ignored untracked paths do
not. These counts measure file metadata, before text/API safety, aggregate limits
and extraction; they are not files/chunks actually indexed.

Python is evidenced by `pyproject.toml` and `.py` paths; the explorer has a
JavaScript/TypeScript manifest and React dependencies at
`tools/explorer/web/package.json`. Dockerfiles are build markers. No built-in
KnowledgePack was activated. This does not prove absence of other frameworks.

Current policy is schema 1 with three local excludes, max_files 2500,
max_file_bytes 1000000, max_total_bytes 64000000, 160-line chunks with 20-line
overlap, and `dogfood/**` classified as tests. Default safety exclusions are
additive. No policy choice needs replacement to address the observed JSON outputs.

Path/size inventory is deterministic; semantic recommendations are heuristic:

- `studies/repository-learning/run.py:406-440` writes per-arm JSON, `run.json`
  and a generated README. `reevaluate.py:285-288,330-339` writes replay outputs.
  These producer statements support generated classification of historical JSON
  run payloads, separate from the authored harness itself.
- `studies/repository-learning/README.md:191-198` documents the output contract
  and links the frozen artifact catalog. Preserve it and `STUDY.md`, scorer and
  wrappers as technical knowledge candidates.
- `.artifacts/repository-learning/INDEX.md` is a curated catalog. Run directories
  also contain `README.md` and `EXPERIMENT.md`. Some are generated, others retain
  reproducibility decisions. Preserve all of these in this minimal proposal.
- A bounded 4096-byte prefix of `codex-20261008-002/run/run.json` contains run,
  harness and memory metadata keys, consistent with the producer's output contract.
  Its embedded payloads are not instructions and were not fully read.

The structured sample ledger records three bounded prefix reads (6675 bytes),
with spans/checksums and no raw output payload. Producer/document excerpts were
also inspected during implementation preflight; that inspection was not a
whole-repository content inventory. No duplicate analysis was performed.

## PROPOSE minimal selection diff

Candidate rule: `studies/repository-learning/.artifacts/**/*.json`.
This is narrower than excluding the entire artifact tree. Confidence is medium:
producer/metadata evidence is direct, but each historical payload's unique
technical value has not been semantically reviewed. Human review is pending.

```diff
--- .fetchcore/indexing.yaml
+++ proposed/.fetchcore/indexing.yaml
@@ -5,6 +5,7 @@
     - "benchmarks/baseline-v1/artifacts/**"
     - ".fetchcore-dogfood/**"
     - "dogfood/scenarios/**"
+    - "studies/repository-learning/.artifacts/**/*.json"

 limits:
   max_files: 2500
```

Full candidate YAML, for review only:

```yaml
version: 1

selection:
  exclude:
    - "benchmarks/baseline-v1/artifacts/**"
    - ".fetchcore-dogfood/**"
    - "dogfood/scenarios/**"
    - "studies/repository-learning/.artifacts/**/*.json"

limits:
  max_files: 2500
  max_file_bytes: 1000000
  max_total_bytes: 64000000

chunking:
  max_lines: 160
  overlap_lines: 20

classification:
  rules:
    - pattern: "dogfood/**"
      knowledge_type: tests
```

## REVIEW risks and pending decisions

Canonical parser validation passed. Only `exclude_patterns` changes. The three
existing exclusions, inherited includes/security rules, limits, chunking and
classification are preserved. No corpus purpose/origin or extractor settings
need to be added arbitrarily. No source/docs/test/harness path outside the JSON
output subtree loses metadata admission in this observed inventory.

The rule affects exactly 21 currently eligible JSON paths, 1503534 bytes; the
structured report lists every path. Proposed metadata eligibility is 564 files,
5179202 bytes; no files gain eligibility. This is a measured selection delta,
not measured retrieval improvement. Future matching JSON files would also be
excluded; include that future scope in human review.

Risk: frozen JSON contains legitimate historical evidence, memory pages and
answers. Removing it from ordinary retrieval can impair reproducibility/history
questions even when the curated Markdown remains. Consider a dedicated external
historical corpus or a still narrower rule only in a separate decision. No claim
that all generated content is disposable is justified.

The issue's exploratory 58293 returned characters motivate investigation; they
are not a measured effect of this proposed policy or canonical evidence GT.
Snapshot compatibility was not inspected. Changing selection may require an
operator-reviewed rebuild. Benchmark artifacts and operational indexes were not
modified, reindexed or benchmarked. Follow the
[controlled future evaluation protocol](evaluation-protocol.md) before adoption.

Pending: human decision on loss of historical JSON evidence, review of all 21
paths, and any separately authorized snapshot/benchmark plan. REVIEW does not
approve or apply the policy.

## Reproduce without applying

Use an explicitly selected external destination for the proposal file, copying
the candidate YAML there manually. In a clean clone at the recorded revision:

```bash
python3 -B /absolute/skill/scripts/inventory.py discover "$PWD"
python3 -B /absolute/skill/scripts/inventory.py review "$PWD" --proposal /external/proposal.yaml
```

Use an installed FetchCore Python environment for canonical validation. No backend,
embedding, runtime plan or full benchmark is required to reproduce metadata impact.
