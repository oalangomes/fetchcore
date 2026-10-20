# P0.1 Developer Local acceptance — 2026-10-19

## Decision and identity

**Local gate: PASS for the isolated lexical Developer Local profile.** Base SHA `96f6146b9bcf22f208d84ea2fd511d85e3081c58` (P0.0 PR #209); P0.1 is draft [PR #210](https://github.com/oalangomes/fetchcore/pull/210), stacked on P0.0. Issues: #200, #99, #98. Product change SHA `608bc0c7d027c4833d11edab0865da0bf8efaae6`. The measured run used its wheel with the MCP extra, installed in a separate venv and executed from `/tmp`. This gate covers one operator, one controlled Git repository and an isolated standalone Solr 9.10.1 resource. It does not claim vector, hybrid, public package, remote MCP or shared backend readiness.

The operator explicitly authorized one temporary Docker project, `fetchcore-p01-codex`, with a private volume and loopback port 18984. Existing `fetchcore-shared`, `fetchcore-operational` and `fetchcore-lab` projects were neither changed nor used. The pinned image digest was `sha256:5ac29f32d689cef9722ec3b9f2c612ed11dce35d9e279ac0f7bd554ad76aa8cb`. The baseline core was `fetchcore-p01-codex-baseline`; the changed wheel used `fetchcore-p01-codex-after`; an empty core was used only for a missing-snapshot failure check.

The baseline was run **before** the product change and frozen as [sanitized JSON](artifacts/p01-baseline-2026-10-19.json). Its private raw JSON SHA-256 is `046d422decd096524e943862c121cc66526967cc8ca08ff14fd3c8620c6a6542`. The [after artifact](artifacts/p01-after-2026-10-19.json) records the changed wheel SHA-256 `8f7caccdbd1c289eaf6633d1e6e0348eff1b53e912776c1b245d5fe5a72b5d83`; its private raw JSON SHA-256 is `a1a45cb63f358ece92e2213ba98647c62e8ca00ebdaa7993e92f7f294dfcece6`. Only whitelisted, non-private fields are published.

## Change and scorecard

The baseline wheel omitted `infra/solr/configset`, requiring a source checkout or separately installed Solr configset for fresh owned provisioning. The changed wheel bundles the same Projection v3 XML files. The existing `solr_runtime prepare` selects this bundled directory only when `provision_if_missing` explicitly creates an owned service and no `--configset-dir` override is supplied. The default `reuse_only` policy, explicit creation flag, backend adapter and retrieval contracts are unchanged. A unit test checks that the bundled files match the original files byte for byte and that owned provisioning passes their installed location to Docker. Wheel inspection confirmed both XML files. The changed wheel was not used to launch an additional live Solr container; the live E2E used the authorized pre-provisioned service.

| Metric | Baseline installed P0.0 | Changed installed P0.1 | Interpretation |
| --- | ---: | ---: | --- |
| CLI commands to first evidence | 8 | 8 | Includes config and doctor checks; no reduction claimed. |
| Operator YAML values | 6 | 6 | Backend, workspace, repository, revision, Solr URL and core. `version` is fixed. |
| Extra checkout configset path for owned provisioning | Required | Not required by installed wheel | Verified by wheel contents and provisioning unit test; live provisioning used the existing service. |
| Harness start to first evidence | 30,651.150 ms | 28,242.111 ms | Excludes installation and backend provisioning. Single nonexclusive-host runs; no causal speed claim. |
| CLI search wall time | 1,281.991 ms | 1,145.614 ms | One query; no retrieval algorithm change. |
| Search-to-source command wall time | 218.061 ms | 202.607 ms | Source content matched the fixture. |
| First MCP startup / search | 1,077.902 / 1,129.751 ms | 515.252 / 965.548 ms | Process startup and tool call measured separately. |
| Second MCP startup / search | 569.509 / 1,034.149 ms | 495.953 / 1,006.252 ms | No causality inferred from a single run. |
| Solr idle CPU/RAM sample | 0.22% / 685.9 MiB | 0.28% / 678.7 MiB | Nonexclusive host, point samples; not steady-state capacity measurements. |
| Solr per-core disk after runs | 136 KiB | 136 KiB | `du -sh` includes core files; shared service overhead excluded. |

The controlled fixture `acme/fetchcore-p01-fixture` produced the same initial corpus checksum `c22b4e4c7319534a61b9f869e34e5e06e971fc0a954b61a82e9cc582196bbeec` and updated checksum `c019f26219f5111e5aaf25bcc9a75d7ce36780f2900a9b36f0ecf997390f9288` in both runs. Its commits differ because each run creates a fresh Git fixture. The indexing fingerprint was `sha256:05b062d902618c1017a6548124dd606bedf2635ec903a4ac0019655a18f9d885`; projection fingerprint was `sha256:80d19b9400141cd7bb73f7ba7f64de414cd5a896b87c2dc1713959804696cef2`. The queried evidence was `repository://acme/fetchcore-p01-fixture/src/widgets.py`, lines 1–2, checksum `sha256:5b6d1517924cac82c276006266c7b272d7cc9c6595a54989f3d38d35394208ed`, with matching CLI source and MCP `get_source` content. These observations prove equivalence for this fixture, not general ranking equivalence.

Both rebuilds wrote two lexical documents and requested zero embeddings. Each committed delta added one, updated one and deleted one document. Repeating `sync` without changes did zero backend writes, zero deletes and zero embedding requests. The harness restarted CLI and MCP processes and retrieved the same initial evidence. The owned Solr service was also restarted with its dedicated volume retained: the baseline core still returned the updated revision; after the changed-wheel run, readiness was `ready`, search returned its updated revision `31c26315f41baef983149dcf3582063254360612`, and source returned `updated widget evidence`.

## Failure and recovery evidence

The installed wheel returned nonzero exits with typed categories: malformed YAML `invalid_runtime_configuration`; unauthorized allowed root `unsafe_source`; unknown repository `repository_not_found`; absent source `source_missing`; wrong requested revision `source_revision_mismatch`. With an unreachable loopback endpoint, `readiness` returned nonzero and `service: unavailable (connection_failure)`; with the isolated empty core, it returned nonzero and `snapshot/currentness: absent (snapshot_missing)`. `index sync` against that empty core failed with `snapshot_missing` and instructed an explicit rebuild. After restoring the valid endpoint/configuration, readiness, search and source succeeded. The failed attempt to create a second core with a configset missing from Solr's server-side directory returned `configset_unavailable`; the owned empty directory was removed, the isolated service restarted, and its CLI created that core from the mounted configset. This exposed a documentation gap: reusing a running standalone Solr service requires a server-side configset for new core creation.

## Reproduction and checks

The [acceptance harness](../../scripts/developer_local_acceptance.py) requires `--ack-owned-resource`, a `fetchcore-p01-codex-` core name, loopback Solr and a new work directory. It does not provision or clean up a backend. Its private raw output includes absolute local paths, so publish only a sanitized artifact. The commands used the installed `fetchcore` and `fetchcore-mcp` scripts, private YAML/cache and a fresh controlled fixture. `python -m pytest tests/unit -q` passed 347 tests; Ruff and mypy passed for the changed Python files; `scripts/check_docs.py` checked 71 Markdown files. Wheel inspection found both bundled XML files. CI for the latest PR head is reported separately after the push.

The pass is limited to the lexical path and controlled fixture. Provisioning from an installed wheel was verified at the package and command boundary but not by creating a second live service. The timing table is a before/after observation, not a performance improvement claim. P0.2 vector acceptance remains gated on isolated vector services and an authorized embedding provider; this P0.1 gate does not authorize writes to those resources.
