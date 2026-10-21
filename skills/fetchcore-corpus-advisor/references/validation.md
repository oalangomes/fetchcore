# Corpus Advisor one-invocation validation — 2026-10-21

This exercise used one user request and no follow-up prompt for either target.
The coding agent followed SCAN → DEEP → INVESTIGATE → PROPOSE → REVIEW in
each repository. It used no subagent, recursive LLM or additional scanner.
Both reviews were read-only with respect to their target repositories;
candidate YAML lived only under `/tmp`. This is behavioral evidence from this
agent, not an independent second-agent trial or a retrieval benchmark.

## RunnerOps: complete review

Target: clean `oalangomes/RunnerOps@5a72fa122d5664e217299e16420fcd7d48b67a92`.
There was no indexing policy or active KnowledgePack.

1. SCAN: 134 paths discovered; 123 eligible and read; 11 not included; no
   truncation. Effective fingerprint
   `sha256:05b062d902618c1017a6548124dd606bedf2635ec903a4ac0019655a18f9d885`.
2. DEEP, run once: 123 sources produced 1,788 documents, with zero files
   without documents and zero exact-source duplicate groups. One JSON test
   fixture was reported as text-only implementation; it is evidence data,
   not a missing JSON symbol extractor. Source origin/purpose remained
   `unknown` in the canonical diagnostics.
3. INVESTIGATE: README.md:11,62,81 and tests/runner/test-runnerctl-contracts.sh:38,61-70
   establish extensionless `runnerctl` as the public CLI. `install.sh:12-17`
   installs it. `runners.conf.example:1-9` documents the registry format and
   `scripts/setup/init-machine-config.sh:41` copies it. `.env.example:1-24`
   documents machine-local defaults. `scripts/systemd/runnerops-systemctl:13-33`
   constrains privileged systemd actions; `scripts/setup/authorize-runtime.sh:8-9`
   installs that helper. All four were `not_included`. The remaining
   unselected paths were license/site assets; `site/styles.css` is used by
   the public website but this review did not establish a need to index it
   for runner-control knowledge.
4. PROPOSE: include those four exact paths. The full new policy would be:

```yaml
version: 1
selection:
  include:
    - runnerctl
    - runners.conf.example
    - .env.example
    - scripts/systemd/runnerops-systemctl
```

   This is a new file proposal. Its diff against absent policy is:

```diff
--- /dev/null
+++ .fetchcore/indexing.yaml (candidate)
@@ -0,0 +1,7 @@
+version: 1
+selection:
+  include:
+    - runnerctl
+    - runners.conf.example
+    - .env.example
+    - scripts/systemd/runnerops-systemctl
```

   No existing rules would be removed. The CLI's canonical
   `--proposal` check passed: 123→127 read paths, exactly those four added,
   zero removed, both scans complete. Fingerprint:
   `sha256:05b062d902618c1017a6548124dd606bedf2635ec903a4ac0019655a18f9d885`
   → `sha256:c335206aa8a3c0de90705e9447b3d9674d788fc867aa6ea932e61240bd3bc28e`.
5. REVIEW: no KnowledgePack conditional defaults were active, but creating
   any explicit policy should still receive operator review. Extensionless
   shell files may be indexed as text without shell structural extraction.
   The scan does not establish document yield or retrieval gains for the
   proposed paths. No exclusion is recommended. The policy was not applied.

The review used one SCAN, one DEEP, one proposal validation, eight path
checks, eight focused path/reference searches and eight bounded source
reads. The agent completed the policy decision without another user prompt.

## FetchCore: complete review

Target: `oalangomes/fetchcore@600651415055e625b2d98c57321263a68846455d`
with only the Advisor SKILL.md modified at observation time. The current
`.fetchcore/indexing.yaml` was read, not edited.

1. SCAN: 723 discovered, 593 eligible, 587 read, 60 explicitly excluded,
   70 not included and 6 blocked API artifacts; no truncation. The effective
   fingerprint was
   `sha256:3dd558e6c73848fa2b5e38738a50f48981a226f27e39b3dc27c2bc364bb57747`.
2. DEEP, run once before further tree edits: 587 read, 579 produced 5,420
   documents. The eight Python files without documents were all zero-byte
   `src/fetchcore/adapters/**/__init__.py`; no extraction defect was found.
   Five checksum-backed duplicate groups were observed.
3. INVESTIGATE: four identical `benchmarks/search-intent-v2/artifacts/**/queries.json`
   are written per run and verified on resume
   (`benchmarks/search-intent-v2/evidence.py:852-860`); the benchmark README
   documents versioned run artifacts (:300-325). They are redundant for
   search but preserve run identity and reproducibility. Two XML configset
   pairs are source/packaged copies needed for installed-wheel provisioning
   (`docs/operations/p01-developer-local-acceptance-2026-10-19.md:13`;
   `tests/unit/operational/test_solr_runtime.py:180-199`). The remaining
   Python pairs are identical compatibility aliases, with public import
   contracts in `tests/contract/architecture/test_module_boundaries.py`.
   These facts do not justify a name-based or blanket duplicate exclusion.
4. The same investigation found four unselected sources with concrete
   consumers: `infra/images.lock` pins CI image inputs
   (`docs/guides/ci-workflows.md:59-62`, `.github/workflows/ci.yml:94`);
   root `.env.example` documents Compose/runtime configuration;
   `tools/explorer/.env.example` documents the Explorer deployment;
   `tools/explorer/docker/nginx.conf` configures the packaged API proxy
   (`tools/explorer/docker/Dockerfile:22`). Canonical `--path` returned
   `not_included` for all four. Existing exclusions for historical baseline
   artifacts, dogfood outputs and scenarios were evaluated and preserved.
   The scenarios contain useful evaluation Ground Truth
   (`dogfood/scenarios/fetchcore-self.yaml:1-39`), but indexing Ground Truth
   into a self-search corpus could contaminate evaluation; this is an inference,
   not a measured retrieval effect.
5. PROPOSE: add only the four selected paths, preserving all existing
   exclusion, limit, chunking and classification rules. Full candidate:

```yaml
version: 1

selection:
  include:
    - "infra/images.lock"
    - ".env.example"
    - "tools/explorer/.env.example"
    - "tools/explorer/docker/nginx.conf"
  exclude:
    - "benchmarks/baseline-v1/artifacts/**"
    - ".fetchcore-dogfood/**"
    - "dogfood/scenarios/**"

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

   The unified diff is one additive block; no other line changes:

```diff
--- .fetchcore/indexing.yaml
+++ candidate/indexing.yaml
@@ -1,6 +1,11 @@
 version: 1

 selection:
+  include:
+    - "infra/images.lock"
+    - ".env.example"
+    - "tools/explorer/.env.example"
+    - "tools/explorer/docker/nginx.conf"
   exclude:
     - "benchmarks/baseline-v1/artifacts/**"
     - ".fetchcore-dogfood/**"
```

   The canonical `--proposal` check passed: 587→591 read,
   exactly those four added, zero removed, both scans complete. Fingerprint:
   `sha256:3dd558e6c73848fa2b5e38738a50f48981a226f27e39b3dc27c2bc364bb57747`
   → `sha256:4e497e1e21517ab39f6adf6a6a64d2199a04280aed02c045a9308a3c91c7a6c0`.
6. REVIEW: the proposal remains unapplied. The CLI measures safe-scan source
   admission, not the documents or retrieval quality the four additions
   would produce. Root `.env.example` includes blank credential fields;
   an operator should review the proposed template content before indexing
   it in a deployed corpus. The observed 16,566 redundant source bytes
   alone do not prove retrieval harm. No exclusion change is recommended.

This review used one SCAN, one DEEP, one proposal validation, seven canonical
path checks, eleven focused path/reference searches and eighteen bounded
source reads. It did not require a second prompt.

## Completion and limits

Both DEEP calls succeeded and each review reached a validated candidate
policy in the same invocation. No target policy, index, snapshot or
infrastructure changed. The scans and deep runs used the local FetchCore CLI,
not OPERATIONAL. The working-tree caveat applies to FetchCore; the reported
counts are for the tree before this validation document was updated.
Candidate-policy extraction and retrieval quality remain unmeasured.
