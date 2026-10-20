# P0.1 Developer Local acceptance — 2026-10-19

## Identification and current result

Phase: P0.1. Base SHA: `96f6146b9bcf22f208d84ea2fd511d85e3081c58` (P0.0 PR #209). Branch: `codex/p01-developer-local`; draft [PR #210](https://github.com/oalangomes/fetchcore/pull/210) is stacked on P0.0. Issues: #200, #99, #98. Environment: Linux host, isolated CPython 3.13.15 wheel venv, Docker Desktop 29.8.2. **Gate: BLOCKED pending an explicitly authorized, isolated Solr test resource.** No shared resource has been written, and no installed-artifact first-evidence result is claimed.

## Existing capability and baseline state

The P0.0 wheel was built from the current FetchCore sources and installed with the MCP extra outside the checkout. `fetchcore --help`, package import and both scripts passed. The current installed CLI has registry, explicit `plan/rebuild/sync`, `search/source`, `doctor/readiness` and YAML v1 loading. The wheel does not contain `infra/solr/configset`, so provisioning a fresh compatible Solr resource without a checkout remains a measured first-use gap. The shared DEV, BENCH and OPERATIONAL resources already running on the host belong to another worktree/execution and are excluded.

The [installed-artifact harness](../../scripts/developer_local_acceptance.py) was prepared **before any product UX change**. It uses a controlled local Git fixture, a new private registry, an operator YAML, an empty isolated Solr collection, CLI search/source, MCP stdio search/get_source, process restart, a committed add/update/delete delta, and repeated sync. It records wall-clock command timings and backend-provided telemetry separately. It refuses to run without `--ack-owned-resource`, an exclusive `fetchcore-p01-codex-` collection name, loopback URL and a new work directory. It never creates or deletes a backend resource. A blocked preflight returned exit 1 and produced `{"status":"blocked"}` before creating the work directory; its two safety unit tests passed.

Read-only preflight found the pinned Solr image digest `sha256:5ac29f32d689cef9722ec3b9f2c612ed11dce35d9e279ac0f7bd554ad76aa8cb` locally. Ports 18984–18986 were not listening. The configset files have SHA-256 `587b917e53e5a54602d0271c841c44c558ace87d035d003a8d5e17b5ebab0576` (`schema.xml`) and `ab372d1659506f59976801089b70f6db708875c8fb05c798187506adc3e6de27` (`solrconfig.xml`). Host inventory showed running `fetchcore-shared`, `fetchcore-operational` and `fetchcore-lab` projects. This preflight proves availability of some ingredients, not permission or an accepted backend.

On commit `9450abc0fce9846ddff1f96d087291f20e1f56d5`, the PR's observed GitHub CI workflows concluded successfully through `runnerctl ci watch . --pr 210 --json` (exit 0). This is a code/packaging gate, not a live backend acceptance result.

## Baseline and before/after scorecard

| Metric | Before | After | Difference |
| --- | --- | --- | --- |
| Commands to first evidence | NOT_MEASURED | NOT_MEASURED | NOT_MEASURED |
| Manual settings | NOT_MEASURED | NOT_MEASURED | NOT_MEASURED |
| Time to first evidence | NOT_MEASURED | NOT_MEASURED | NOT_MEASURED |
| Time from search to source | NOT_MEASURED | NOT_MEASURED | NOT_MEASURED |
| CLI/MCP startup | NOT_MEASURED | NOT_MEASURED | NOT_MEASURED |
| Idle CPU/RAM/storage | NOT_MEASURED | NOT_MEASURED | NOT_MEASURED |
| Failure recovery | NOT_MEASURED | NOT_MEASURED | NOT_MEASURED |

Run the baseline with the installed P0.0 artifact and a dedicated, empty resource **before** modifying first-use UX. Freeze its JSON output. Repeat with a different owned resource and changed artifact. The harness does not estimate missing measurements. Example invocation after explicit resource authorization and provisioning:

```bash
python scripts/developer_local_acceptance.py \
  --cli /absolute/venv/bin/fetchcore \
  --mcp /absolute/venv/bin/fetchcore-mcp \
  --work-dir /absolute/new/private/fixture-run \
  --output /absolute/private/baseline.json \
  --solr-url http://127.0.0.1:18984/solr \
  --collection fetchcore-p01-codex-baseline \
  --ack-owned-resource
```

The harness output includes local absolute paths and is a private raw artifact. Publish a sanitized summary with the source SHA, fixture revision, Solr resource identity, fingerprint observations, executed commands/exits, timings and test results. Verify a returned `source_ref` against the controlled source before claiming first evidence.

## Gate and next phase

No code path was changed to compensate for a missing operational resource. The harness is preparatory work, not evidence of a passed golden path. P0.1 remains **BLOCKED** until the installed baseline, implementation, real CLI/MCP journey, persistence, incremental/no-op, failure matrix and after measurements pass. **P0.2 acceptance: STOP**; only independent read-only capability/resource preflight may proceed. If the isolated resource is authorized, run the baseline first and update this report with actual evidence and a new gate decision.
