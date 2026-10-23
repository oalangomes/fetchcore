# Corpus Advisor stabilization — 2026-10-22

This is behavioral evidence for PR #222, not a retrieval benchmark. One user
request drove SCAN → DEEP → INVESTIGATE → DRAFT POLICY → CRITIC → RECONCILE
→ VALIDATE → REVIEW. No target policy or index was applied.

## Fixed inputs and canonical observations

The real target was a clean, detached temporary clone of
`oalangomes/fetchcore@63537a35ab198dd4e7b1e452bd5a66f87dda19f7`. The CLI was
`/home/operator/projetos/GitHub/fetchcore/.venv/bin/fetchcore`, using the
stabilized source in this PR. Target source content stayed frozen while the
Advisor instructions and CLI regression fix were edited in the working copy.
No historical policy recommendation was treated as a current approval.

Artifacts live outside the repository at
`/tmp/fetchcore-advisor-final-dywv5yvt/`: `baseline.json`, `scan.json`,
`deep.json`, `path-checks.json` and the final proposal artifacts. This report
records the reviewable decisions, full YAML, diff and canonical impact; temporary
artifact availability is limited to the current environment.

| Observation | Confirmed result |
| --- | --- |
| SCAN, once | 736 discovered; 604 eligible; 598 read; 6,863,106 source bytes |
| Admission skips | 60 excluded; 72 not included; 6 API artifacts quarantined |
| DEEP, once | 590 sources produced 5,490 documents; 8 read without documents |
| Empty sources | Eight zero-byte `src/fetchcore/adapters/**/__init__.py`; no extraction loss established |
| Duplicates | Five checksum groups: query copies, two source/package XML pairs, two compatibility alias pairs |
| Coverage | No scan/extraction truncation; 26 implementation-classified files have text-only coverage |
| Capabilities | No active KnowledgePack; canonical default extractors; binary document policy disabled |

The text-only bucket comprises JSON and Markdown content, not evidence of a
missing code-symbol provider. Purpose and origin remain `unknown` where no
classification supplies them. The SCAN source-path **sample** is truncated to
50; that is distinct from scan traversal, which is complete. Counts are source
admission/extraction observations and do not measure retrieval quality.

## Recommended changes

Recommend the root environment template, Explorer Nginx routing and Explorer
CSS. **Hypothesis:** admitting them will make current/legacy configuration
wiring, proxy timeouts/SPA fallback and focus/selection/responsive behavior
easier to retrieve. That benefit is unmeasured. **Risk:** more source
content/context, with no measured ranking or latency effect. The root basename
include is constrained by a new broad exclusion of **all nested**
`.env.example` paths; the other two includes are literal relative paths.
Existing broad exclusions remain intact.

## Pending decisions

The two investigated baseline run reports remain `needs_review`, outside the
candidate. Includes cannot override their retained broad exclusion.
Replacing an area exclusion with today's files would not protect future files.
The root template's initial pending status was resolved by new canonical
matching evidence, recorded below; it is no longer a pending decision.

The concrete operator choice is: **keep these sources outside search, or
separately authorize a concise technical document in an already admitted
documentation area?** This review keeps their existing selection. Neither an
unanswered question nor approval of PR #222 applies a corpus policy. Authoring
that alternative documentation is outside this stabilization round.

## Rejected changes and six-criterion assessment

Every candidate below has one final decision. In the table, **K/U/D** are
rubric criteria 1–3: technical knowledge, unique contribution relative to
selected alternatives, and redundancy. **R** is criterion 4. Every row's
paths/spans provide criteria 5–6: provenance and verifiable evidence, all tied
to the frozen repository/revision above. Uniqueness is bounded by the inspected
alternatives, not an exhaustive corpus-wide claim. Proposed retrieval benefits
remain hypotheses; source facts and CLI admission are confirmed evidence.

| Candidate and final decision | Confirmed K/U/D and alternative-source check | R and remaining hypothesis/uncertainty |
| --- | --- | --- |
| `tools/explorer/docker/nginx.conf`: **include** | `:7-16` defines proxy version/headers, 5s/65s timeouts and SPA fallback; selected `Dockerfile:22` only copies it. These details were absent from inspected selected alternatives. | Adds operational text, no sensitive values observed; retrieval benefit unmeasured. |
| `tools/explorer/web/src/style.css`: **include** | `:69-109` defines focus, active-result and responsive behavior; selected `App.tsx:200` references active classes and `main.tsx:5` imports styles but neither defines those rules. | Adds UI context; import alone was insufficient justification. Retrieval benefit unmeasured. |
| `infra/images.lock`: **no_change** | `:1-4` pins CI inputs, consumed at `.github/workflows/ci.yml:94`; selected `docs/guides/ci-workflows.md:59-63` already explains ownership/update behavior, and `infra/compose/app.yml:24` plus `infra/compose/solr.yml:4` duplicate the pins. | Initially investigated for inclusion; alternative-source evidence did not establish a meaningful unique contribution. Not in candidate. |
| Root `.env.example`: **include** | `:1-62` combines source-mount semantics, current/legacy wiring caveats and concrete examples beyond the inspected selected runtime/Compose descriptions. Credential fields inspected were blank/commented. | Unconstrained basename admission would widen scope. Pair it with `**/.env.example` exclusion to keep every nested template outside search. Retrieval benefit unmeasured; future contents still require ordinary source review. |
| `tools/explorer/.env.example`: **no_change** | `:1-9` repeats root defaults `:7-12`; selected Compose supplies the same runtime defaults. No distinct engineering contract established. | No inclusion justified; excluding only this known copy would not constrain future basename matches. |
| `benchmarks/baseline-v1/artifacts/V0.3/README.md`: **needs_review** | `:8-40` records run revision/corpus and measured quality/latency/volume. Selected `docs/benchmarks/baseline-v1.md:315-335` explains report generation/provenance but does not reproduce these run aggregates. | Current exclusion protects the whole artifact area. Report inclusion cannot override it; narrowing that exclusion would admit future artifacts. Keep excluded. |
| `benchmarks/baseline-v1/artifacts/V0.4/README.md`: **needs_review** | `:8-54` adds run-specific retrieval and indexing cost aggregates; selected protocol documentation explains the metric contract, not these exact aggregates. | Same broad protection must remain. A separate reviewed summary is compatible; no move, rewrite or policy application here. |
| Four `benchmarks/search-intent-v2/artifacts/**/queries.json` copies: **preserve** | Canonical DEEP confirms identical checksum; `evidence.py:840-861` writes/verifies run-specific query artifacts on resume. Selected `evidence-queries.json` supplies canonical query intent. | Redundancy confirmed; retrieval harm and acceptable provenance loss not established by duplication alone. Preserve current admission, without carrying historical exclusion approval forward. |
| XML source/package pairs: **preserve** | `infra/solr/configset/conf/{schema,solrconfig}.xml` and `src/fetchcore/operational/configset/conf/` duplicate content; `pyproject.toml:42` packages them and `test_solr_runtime.py:180-199` verifies bundled provisioning. | Same content but distinct source/package provenance; no evidenced benefit justifies source exclusion. |
| Two Python alias pairs: **preserve** | `src/fetchcore/{backends/solr_document_incremental,repository/document_incremental}.py` and corresponding incremental-persistence aliases expose public compatibility paths; `test_module_boundaries.py:59,227-228` tests them. | Exclusion could hide a supported import path; repeated source bytes alone do not justify it. |
| `dogfood/scenarios/**`: **preserve** | `fetchcore-self.yaml:7-45` contains expected-path labels; `benchmarks/search-intent-v2/run.py:100-125` uses them for measurement. Selected implementation/docs describe runtime behavior separately. | Evaluation contamination is an inference, not a measured effect; retain existing exclusion and evaluation inputs in Git. |
| Eight empty Python package initializers: **preserve** | Focused size checks confirm the eight zero-byte `src/fetchcore/adapters/**/__init__.py` paths; DEEP reports eight Python files without documents. There is no source content for a selected alternative to replace. | No extraction defect established; keep Python admission and package provenance. |

All candidates examined earlier in this invocation are accounted for. The lock
file left the inclusion consideration because of new alternative-source
evidence; report exceptions remain pending; duplicate exclusions lack sufficient
evidence in this run. The root template moved from pending to include after
canonical rule-combination evidence overturned an earlier inference. No decision
history store was added.

## Independent critique and reconciliation

The native `/root/corpus_policy_critic` received repository identity, fixed
policy/capabilities, SCAN/DEEP diagnostics and candidate paths without the
parent's proposed decisions. It performed four focused searches and ten bounded
source reads, without rerunning SCAN/DEEP or writing files. The parent then
shared its draft with the same critic for a second-stage challenge.

All twelve draft decisions received `supported` verdicts in stage two; no
material conflict remained. The critic's first-pass `preserve` for the nested
template and the parent's `no_change` retained the same nonselection. This was
a terminology difference, not forced evidence agreement. Reconciliation:

- Accept the challenge that copying/importing a file proves use, not unique
  value. Keep the two includes because the actual routing and focus/layout
  rules answer questions missing from the inspected selected consumers.
  Accept the CSS context-cost objection as a limit, without inventing metrics.
- Accept the lock-file alternative-source objection; no change recommended.
- Accept the future-path objections to an unconstrained basename include and
  report exceptions. At this point, both remained outside the first candidate;
  the three original broad exclusions stayed intact.
- Accept that duplication alone does not establish tolerable loss of run,
  package or import-path evidence. Preserve all three duplicate categories.
  Original query-input equivalence was not fully verified in this invocation.
- Accept that scenarios hold legitimate technical knowledge; preserve their
  existing exclusion while identifying contamination as a hypothesis.
  Empty initializers establish no extraction defect; preserve selection.

The final self-check then found a safe expression in the existing contract:
include `.env.example`, exclude `**/.env.example`. The initial inference that
root-only selection was impossible was too strong. Three canonical path probes
confirmed root `not_included → read`, current nested template
`not_included → excluded`, and hypothetical future nested template
`not_included → excluded`. No target file was created for the hypothetical
probe. Exclusions run first, and the nested exclusion requires a slash.
Unlike a list of today's copies, this rule protects every future nested copy.

The parent returned this new evidence and revised root decision to the **same**
critic, which supported inclusion and explicitly corrected its earlier
inference. Its strongest objection was partial overlap with selected runtime
documentation. The parent accepted that limit, judged the template's concrete
wiring/caveats sufficient, and accepted the requirement to disclose the new
all-nested exclusion and changed skip reason. No additional agent or review
stage was introduced; the existing investigation/reconciliation loop resolved
the finding before final revalidation.

The critic made no additional searches/reads after stage one. The parent's target
investigation used eight focused searches, eighteen bounded source spans and
thirteen canonical path checks, in addition to setup/policy/CLI implementation
inspection. The read/search ceilings were not exhausted by the parent; no
consequential technically resolvable question was deferred to another prompt.

## Candidate YAML and minimal diff

The proposal is external and unapplied. The full candidate retains all existing
fields and their order:

```yaml
version: 1

selection:
  include:
    - ".env.example"
    - "tools/explorer/docker/nginx.conf"
    - "tools/explorer/web/src/style.css"
  exclude:
    - "benchmarks/baseline-v1/artifacts/**"
    - ".fetchcore-dogfood/**"
    - "dogfood/scenarios/**"
    - "**/.env.example"

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

```diff
--- .fetchcore/indexing.yaml
+++ /tmp/fetchcore-advisor-final-dywv5yvt/proposal.yaml
@@ -1,10 +1,15 @@
 version: 1

 selection:
+  include:
+    - ".env.example"
+    - "tools/explorer/docker/nginx.conf"
+    - "tools/explorer/web/src/style.css"
   exclude:
     - "benchmarks/baseline-v1/artifacts/**"
     - ".fetchcore-dogfood/**"
     - "dogfood/scenarios/**"
+    - "**/.env.example"

 limits:
   max_files: 2500
```

## Canonical final impact and integrity

Two proposal comparisons ran against the unchanged target: the first reconciled
candidate contained the two Explorer paths; the final candidate additionally
resolves root-template admission with the verified nested exclusion. DEEP was
not repeated because neither target workspace nor current policy changed.
The final comparison ran after the additional reconciliation:

```bash
/home/operator/projetos/GitHub/fetchcore/.venv/bin/fetchcore index \
  --workspace /tmp/fetchcore-advisor-final-dywv5yvt/repo corpus \
  --proposal /tmp/fetchcore-advisor-final-dywv5yvt/proposal.yaml --json
```

- Read paths: **598 → 601**; eligible: **604 → 607**.
- Added: exactly root `.env.example` (2,926 bytes),
  `tools/explorer/docker/nginx.conf` (366 bytes) and
  `tools/explorer/web/src/style.css` (6,715 bytes).
- Removed: **none**. Source bytes: **6,863,106 → 6,873,113** (+10,007).
- Before/after scan truncation: **false / false**; `applied: false`.
- Before fingerprint:
  `sha256:3dd558e6c73848fa2b5e38738a50f48981a226f27e39b3dc27c2bc364bb57747`.
- Candidate fingerprint:
  `sha256:2012da75682ee2314765b9b62ecb383e6eb4a16680ba0882b2f90a2f81102884`.

The two literal slash-containing includes cannot match an unrelated basename;
the root basename include is constrained by the all-nested exclusion. The
Explorer template remains unselected but changes skip reason from
`not_included` to `excluded`; excluded paths count **60 → 61**, not-included
paths **72 → 68**. The three original broad exclusions, defaults, limits,
chunking and classification are unchanged, including future artifact protection.
Current-path impact alone was not used to justify this rule-level conclusion.

After all modes, target `git status --porcelain --untracked-files=all` remained
empty. `.fetchcore` still contained only the original `indexing.yaml`;
its SHA-256 remained
`607a02cfe741d3d2734c390c5a1b962d77ce4c624c7fcb236bf8369b350fcc4a`.
The Git index SHA-256 also matched its before value. No snapshot, embedding,
index, policy or source file was created/changed in the target. The main
working copy's indexing policy was likewise not edited.

Raw external JSON SHA-256 for audit:

| Artifact | SHA-256 |
| --- | --- |
| `scan.json` | `3b9adbbde7bdb0d2cdfbcc6fd14c0e5a4b1373757995cf00f258bcbacd0d4382` |
| `deep.json` | `6a7655d92821ea4b88d4af45b2fc82ef71cc87cd32a4ae980b3a4391e26aa168` |
| `proposal.json` | `7354d223544a873bea256abd7ddb83087e97f8978d6c3cc4a0d63dfd2fb46969` |

## CLI contract and regression validation

SCAN calls canonical `scan_repository_text` with the runtime indexer's effective
selection, limits and API sanitizer. DEEP calls canonical `indexer.index` in
memory. PROPOSAL loads an override through the canonical policy loader, scans
current/candidate admission and compares read paths and fingerprints. The CLI
never instantiates a backend, registry, embedding provider or persisted index.
Path validation and API quarantine stay inside the canonical scanner.

A reproduced defect was fixed: YAML `false`, `0` and `[]` were coerced into an
empty mapping by `safe_load(...) or {}`, silently loading defaults. Both text
and document policy loaders now reject non-mapping values. Empty/null YAML
retains its prior empty-policy meaning. No glob, precedence, schema version,
fingerprint identity or retrieval behavior changed for valid policies.

Existing and new CLI tests cover invalid current/document/proposal policies,
empty-policy compatibility, exclusion precedence, future-path counterexamples,
basename widening, quarantine, traversal/symlink rejection, no network access
and byte-for-byte workspace preservation across SCAN/DEEP/PROPOSAL.

- CLI contract suite: **28 passed**.
- Full suite in a clean clone with the code/test patch: **1,187 passed,
  9 skipped** (opt-in external-service tests).
- The original local full-suite run had **1 failure, 1,186 passed, 9 skipped**:
  a pre-existing ignored `benchmarks/baseline-v1/artifacts/V0.2/.run-state.json`
  violated the frozen-directory inventory assertion. The file was preserved;
  it is absent from the clean validation checkout. No test was weakened.
- Ruff, mypy (182 source files), skill metadata validation and documentation
  link checks passed; `git diff --check` passed.

## Limits and completion

This is one native-agent review of one real repository, with one independent
critic, not a guarantee of deterministic future LLM decisions. No retrieval
benchmark was requested or run. Candidate extraction yield, retrieval quality,
ranking, latency and embedding effects remain unmeasured. Pending policy choices
are explicit and preserve current admission; they do not block review of the
Advisor stabilization itself. No new scanner, parser, agent role, workflow,
policy mechanism or retrieval feature was introduced.

Files changed in this stabilization: `skills/fetchcore-corpus-advisor/SKILL.md`,
its `references/critic-review.md` and this report;
`src/fetchcore/repository/{index_policy,document_policy}.py`;
`tests/contract/cli/test_corpus_cli.py`; `docs/reference/cli-mcp.md`.
