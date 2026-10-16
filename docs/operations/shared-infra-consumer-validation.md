# Shared infrastructure consumer validation (PR #183)

This report covers the branch after merging the current `master`. The initial
[consumer audit](shared-infra-consumer-audit.md) was written before implementation.
`PASS` means the stated surface was verified at the named boundary. `BLOCKED`
means the external execution prerequisite is absent; it is not a green runtime
claim. `NOT APPLICABLE` means no separate live service exists at that boundary.

| Consumer | Configuration migrated | Tests | Real smoke | Compatibility |
| --- | --- | --- | --- | --- |
| Compose | PASS: app overlay attaches API/MCP to the existing environment network; web keeps project private DNS; DEV/BENCH ports and resources differ. | PASS: routing, ownership, network and Compose rendering. | PASS: one shared Solr instance, separate DEV/BENCH app projects; restart retained the BENCH snapshot and fingerprint. | PASS: root and explorer legacy entry points remain for CI. |
| CLI | PASS: explicit runtime and index settings select the same backend/resource; repository registration remains separate. | PASS: CLI contract suite and routing tests. | PASS: registered `oalangomes/NeuroTrack_MS`, retrieved from BENCH Solr with the expected revision and source reference. | PASS: legacy CLI inputs still load. |
| API | PASS: Search Lab target allowlist binds repository, backend, resource and environment. | PASS: API contract and authorization tests. | PASS: BENCH container API returned the demo source; TestClient against the real NeuroTrack BENCH core returned the same source reference as CLI/MCP. | PASS: API request shape and existing target default retained. |
| MCP | PASS: stdio and Streamable HTTP use the same application service binding. | PASS: MCP contract suite. | PASS: stdio search plus `get_source` on NeuroTrack; HTTP discovery/search/source on the BENCH demo target. | PASS: stdio entry point remains; HTTP remains optional local adapter. |
| Search Lab | PASS: authorized environment, backend, repository, revision, resource, capabilities, readiness checks and fingerprints are exposed. | PASS: Vitest and Playwright, including unavailable target behavior. | PASS: BENCH and DEV pages used their own API targets; BENCH search returned a source; absent OPERATIONAL snapshot remained not ready. | PASS: no arbitrary browser endpoint or repository selection. |
| Baseline benchmarks | PASS: new-run config resolves BENCH resources while frozen runner/manifest/ground truth remain unchanged. | PASS: config, routing, byte identity and observed fingerprint tests. | PASS: real Solr lexical run on BENCH NeuroTrack corpus, 20 queries × 20 repetitions; evidence recall 0.333333. | PASS: historical runner SHA-256 remains `28dce2a372175862ed67375c5164de13c81865f37ec127521d3c19a66e4ebd93`. |
| SearchIntent benchmarks | PASS: new runs require BENCH, owner and Solr; comparable performance requires dedicated BENCH. | PASS: guardrail and existing evidence tests. | PASS: one real NeuroTrack BENCH scenario, expected path hit 1/1. | PASS: explicit historical reproduction path retained. |
| Repository learning | PASS: FetchCore wrapper inherits the explicit Solr BENCH endpoint/resource and rejects unsupported backend selection. | PASS: wrapper routing and existing protocol tests. | BLOCKED: end-to-end study requires a fresh external Codex and ai-memory run, which was not launched as infrastructure smoke. | PASS: historical default and artifacts remain available. |
| Hosted CI | PASS: self-dogfood and explorer retain run-owned ephemeral Solr and existing triggers. | PASS: workflow structure and gate execution tests. | PASS: `runnerctl ci watch . --pr 183 --json` reported all three observed workflows successful on commit `8300793`. | PASS: no local developer endpoint became a hosted dependency. |
| Operational Actions | PASS: isolated URL/resource, `reuse_only`, no implicit creation, compatible existing snapshot gate before sync. | PASS: workflow caller and pass/fail gate tests. | BLOCKED: the isolated operational Solr instance has no indexed core/snapshot; explicit operator provisioning and migration are required. | PASS: ephemeral hosted caller does not enable the persistent snapshot gate. |
| SDK | PASS: the existing in-process application service consumes the same explicit runtime binding. | PASS: application and backend contract suites. | PASS: direct `load_runtime_config` plus `service_from_runtime_config` search returned the same BENCH NeuroTrack source reference as CLI/API/MCP. | PASS: public contracts and persisted identities unchanged. |
| Documentation | PASS: daily DEV/BENCH/OPERATIONAL paths, Search Lab, benchmark policy, self-hosted migration and legacy scope are documented. | PASS: link and documentation checks. | NOT APPLICABLE: operational commands above were exercised at their respective surfaces. | PASS: frozen and migration documents retained. |

## Live boundaries

- Shared Solr: `127.0.0.1:18983/solr`; DEV and BENCH use separate `fc-dev-*`
  and `fc-bench-*` logical names. The routed BENCH resource was
  `fc-bench-neurotrack_ms-5f6a70bb2b4b3145`, with 9,002 evidence documents
  plus one snapshot. The BENCH NeuroTrack snapshot revision is
  `74ce3244956a65baed61f1dae6f498cb2be4f3b7`. Its observed projection
  fingerprint is `sha256:cfd4a63f85c8e4034bf17f0f82f08a47c717d52e75a9180ffb1eb6cad719e039`.
  The BENCH indexing fingerprint was
  `sha256:d26ef0d0bce772ba657a9a42a7260035b8ec0c6579bc079a1f0e470e40dc336e`.
  The snapshot, revision, projection fingerprint and corpus checksum were unchanged
  after restarting the shared Solr container. A repeat `up` still left exactly
  one shared Solr container.
- The isolated operational Solr at `127.0.0.1:38983/solr` had zero cores before
  and after the BENCH run. The DEV core was absent. No benchmark write targeted
  either environment. The BENCH host was shared, so its latency observations are
  **functional only**, not comparable performance claims.
- The final baseline run recorded 400 observations, 240 recovered Ground Truth
  requirements and mean EvidenceRecall@20 of 0.333333. The final SearchIntent
  run against that same core recovered its single reviewed path (hit 1/1,
  ExpectedPathMRR 0.5). Both runs recorded the exact corpus revision and BENCH
  owner; the baseline sidecar contains the observed indexing and projection
  fingerprints. Lexical retrieval did not invoke an embedding serializer/model.
- An earlier functional smoke used `fc-bench-neurotrack_ms-8883c43a1a27d600`
  before mixed-case routing was corrected. It remains in the shared BENCH volume
  as an unreferenced resource. No core or volume was deleted during this work.
- A Search Lab health response means only that the API is serving. The live
  BENCH demo target can retrieve a source while readiness remains not ready when
  currentness/fingerprint evidence is incomplete. The UI exposes the checks
  instead of promoting a search hit to an operational readiness claim.
- The self-hosted operational workflow cannot be enabled safely until an
  operator explicitly prepares and indexes its isolated resource. The gate
  deliberately fails before sync when that snapshot is absent or incompatible.

## Validation commands

`make compose-config`, Ruff, mypy (178 source files), Python package wheel/sdist
build and `scripts/check_docs.py` (63 Markdown files) passed. `python -m pytest -q`
passed with 1,021 tests and 7 skips. The frontend passed 61 Vitest tests,
TypeScript/Vite build and 8 Playwright tests against a real BENCH Solr core.
`git diff --check` passed. The live BENCH output and sidecar were written outside
the repository under `/tmp/fetchcore-bench-routed-183/V0.1/`; no frozen artifact
was overwritten. Repository-mapped runner preflight passed. The remote PR watcher
reported success for all three observed workflows on commit `8300793`; the
OPERATIONAL deployment workflow remains separately blocked by its absent snapshot.
