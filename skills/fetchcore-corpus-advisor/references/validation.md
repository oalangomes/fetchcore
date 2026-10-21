# Corpus Advisor validation — 2026-10-21

This is a read-only smoke of the coding-agent workflow, plus focused command
tests. It is not a retrieval benchmark or an independent second-agent trial.

## CLI capability audit

| Existing capability | CLI before this change | Advisor use | Actual gap |
| --- | --- | --- | --- |
| Repository registry | `repo list/inspect`; may require operator cache | Optional context for registered targets | Cannot inspect an arbitrary unregistered workspace |
| Effective policy, KnowledgePacks, fingerprint | `index status/plan` through operational runtime | Configuration evidence | No local backend-free command |
| Safe scan and source-kind coverage | In `RepositoryIndexer.index` | FAST coverage and path admission | No local CLI view |
| Source/language coverage and exact-source duplication | In canonical extraction or persisted documents | DEEP investigation | No backend-free CLI view |
| Snapshot compatibility and planning | `index status/plan` | Only supplied, target-verified operational evidence | Registration/backend required; plan may materialize and extract |

`index corpus` now delegates to the existing policy loader, pack selection,
safe scan and indexer. Its default mode reads bounded source files but creates
no canonical documents. `--deep` creates canonical documents in memory and
reports existing telemetry and duplication diagnostics. `--proposal` validates
external YAML with the same loader and compares paths actually read. It neither
persists a snapshot nor measures retrieval.

## RunnerOps smoke

Source: `oalangomes/RunnerOps@5a72fa122d5664e217299e16420fcd7d48b67a92`,
clean shallow clone. The agent ran one aggregate scan, six candidate-path
checks, one deep extraction, and bounded reads of instructions, README, public
CLI, website and the configuration/test evidence. No repository policy exists.

- Canonical scan: 134 discovered, 123 eligible and read, no truncation.
- `runnerctl`: `not_included`. The README declares it the stable public CLI
  (README.md:11,62,81,101-123); the file implements command routing
  (runnerctl:1-110), and tests copy and exercise it
  (tests/runner/test-runnerctl-contracts.sh:1-85). The extensionless filename
  explains admission, not value. The current language detector maps `.sh`
  but not extensionless shell files, so inclusion would provide textual
  evidence without claiming structural shell extraction.
- `runners.conf.example`: `not_included`. It documents the registry format
  (runners.conf.example:1-9) and the setup script copies it as the local
  configuration template (scripts/setup/init-machine-config.sh:41).
- `scripts/runner/lifecycle.sh`, `configure.sh` and the `runnerctl` contract
  test: `read`; the default corpus preserves these.
- `site/styles.css`: `not_included`. It styles a public website referenced
  by site/index.html:14; no evidence from this review makes it necessary for
  understanding the runner control plane. Decision: `no_change`, not a
  name-based exclusion.
- External policy with `selection.include: [runnerctl, runners.conf.example]`
  passed canonical validation. The observed scan changed 123 to 125 read
  paths, adding exactly those two and removing none; both scans were complete.
  The fingerprint changed. No policy was applied, and retrieval impact is
  unmeasured. Recommendation: include both for review.

Deep mode produced documents for 123 sources and observed zero exact-source
duplicate groups. It cannot assess duplication in unselected paths.

## FetchCore smoke

Source: this PR workspace at HEAD `79faded` plus uncommitted refactor. Its
`.fetchcore/indexing.yaml` already excludes
`benchmarks/baseline-v1/artifacts/**`, `.fetchcore-dogfood/**`, and
`dogfood/scenarios/**`. The canonical path check confirms the first rule;
the text of the policy, not a directory label, grounds the exclusion.

A FAST scan observed 729 discovered, 599 eligible and 593 read, without
truncation. A subsequent DEEP extraction on the working tree observed 586
read, 578 producing documents and five exact-source duplicate groups. The
working tree changed during the refactor, so these counts are separate
observations rather than a scan-to-extraction conversion rate. Duplicate
signals alone do not justify a new exclusion. Recommendation: `no_change`
to FetchCore policy; examine the five groups if optimizing corpus density
later. No policy or index was changed.

## Efficiency and limits

The PR's previous support code had 580 lines in `inventory.py` and 654
helper tests, plus a 201-line skill entrypoint and 1,000+ lines of references.
This revision removes the helper and its parallel `area_summary`; the CLI
addition is a thin interface over existing contracts, with focused contract
tests. In the RunnerOps smoke, the model consumed a filtered aggregate JSON
view rather than 134 per-path inventory rows. Tool calls, read spans and
context size were not measured under a controlled A/B run against the prior
PR, so no token or latency gain is claimed. FAST and DEEP have explicit
finite budgets in SKILL.md.

Neither smoke used OPERATIONAL, registration, embeddings, sync, rebuild,
persisted indexing, or a retrieval benchmark. `--deep` ran extraction in
memory only. The global personal symlink remains untouched.
