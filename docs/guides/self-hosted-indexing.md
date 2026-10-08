# Self-hosted repository indexing

FetchCore can index a repository from GitHub Actions on a self-hosted runner while keeping indexing semantics inside FetchCore.

```text
GitHub Actions
    ↓
fetchcore index status / dry-run / sync
    ↓
RepositoryIndexRuntime
    ↓
TargetedGitAwareSolrSynchronizer
    ↓
Solr snapshot + repository knowledge
```

The workflow is transport and scheduling only. It does not reimplement Git delta discovery, fingerprint compatibility, canonical indexing, or persistence rules.

## Why Solr is the first operational backend

FetchCore currently has proven targeted incremental persistence for Solr. OpenSearch and Qdrant still prefer safe full rebuilds in their current `sync()` paths. The workflow therefore uses Solr rather than calling a full rebuild "incremental".

## Operational CLI

The public automation surface is:

```bash
fetchcore index \
  --workspace . \
  --repository owner/repository \
  --branch main \
  --revision "$(git rev-parse HEAD)" \
  --backend solr \
  --backend-settings '{"url":"http://127.0.0.1:8983/solr","collection":"repo-knowledge"}' \
  status

fetchcore index ... dry-run
fetchcore index ... sync
```

Every successful command emits JSON. `bootstrap` validates the active Solr collection against the FetchCore Projection v3 schema. Managed Solr is provisioned from `infra/solr/configset`, which preserves the behavior-proven analyzers, BM25 parameters and vector field contract extracted from AgentsOrchNext. The CLI never silently rewrites an incompatible schema. `dry-run` performs no persistent writes.

Equivalent environment variables are available:

- `FETCHCORE_INDEX_WORKSPACE`;
- `FETCHCORE_INDEX_REPOSITORY`;
- `FETCHCORE_INDEX_BRANCH`;
- `FETCHCORE_INDEX_REVISION`;
- `FETCHCORE_INDEX_BACKEND`;
- `FETCHCORE_INDEX_BACKEND_SETTINGS`.

The repository-targeted [indexing lifecycle](repository-indexing.md) is available
in parallel. Existing workflows retain this explicit workspace/identity surface,
including its initial/recovery rebuild behavior. Registered `sync <repo>` requires
an existing compatible index and never inherits that recovery policy.

## Reusable workflow

The repository provides `.github/workflows/repository-index.yml` as a reusable workflow and manual workflow.

An adopting repository can call:

```yaml
jobs:
  index:
    uses: oalangomes/fetchcore/.github/workflows/repository-index.yml@master
    with:
      runner_labels_json: '["self-hosted","Linux","X64"]'
      target_repository: ${{ github.repository }}
      target_ref: ${{ github.sha }}
      target_branch: ${{ github.ref_name }}
      collection: my-project-knowledge
      managed_solr: true
      solr_volume: my-project-fetchcore-solr
```

A complete example lives at `examples/github-actions/fetchcore-index.yml`.

## Runner selection

`runner_labels_json` is a JSON array passed directly to `runs-on`.

For FetchCore self-dogfood, set the repository variable:

```text
FETCHCORE_INDEX_RUNNER_LABELS_JSON
```

Example:

```json
["self-hosted","Linux","X64","local-runner","fetchcore"]
```

If the variable is absent, the self-dogfood workflow defaults to:

```json
["self-hosted","Linux","X64"]
```

This keeps runner affinity outside FetchCore domain contracts.

## Persistence modes

### Managed local Solr

`managed_solr: true` starts pinned Solr `9.10.1` on the selected self-hosted runner, creates the requested collection from the FetchCore configset in `infra/solr/configset`, and mounts a named Docker volume.

```text
workflow run N
    ↓
named Docker volume
    ↓
Solr snapshot revision N

workflow run N+1
    ↓
same named volume
    ↓
Git delta N..N+1
    ↓
targeted incremental persistence
```

This provides real incremental behavior only while the runner/host retains that Docker volume.

### External Solr

For ephemeral runners or shared infrastructure, the external service must already expose the requested collection with a schema compatible with the FetchCore configset. The workflow validates that contract before indexing:

```yaml
managed_solr: false
solr_url: http://solr.internal:8983/solr
```

The external service owns persistence. FetchCore still owns snapshot compatibility and incremental indexing semantics.

## Workflow evidence

Each run executes:

```text
explicit schema bootstrap/validation
→ status before
→ dry-run
→ sync
→ status after
```

Every piped command enables `pipefail`, so report capture with `tee` cannot turn a failed FetchCore command into a successful workflow step.

and uploads JSON artifacts for each stage. Writers are serialized per repository/collection through GitHub Actions concurrency.

## FetchCore self-dogfood

`.github/workflows/self-dogfood.yml` runs after pushes to `master` and can also be dispatched manually.

It indexes `oalangomes/fetchcore` into the `fetchcore-self` collection, keeps the Solr state in the `fetchcore-self-solr` Docker volume, and then evaluates the index through the real MCP stdio server.

The flow is:

```text
FetchCore source
→ self-hosted runner
→ incremental FetchCore index
→ persisted Solr snapshot
→ fetchcore-mcp
→ MCP client
→ reviewed repository-local scenarios
→ JSON + Markdown PASS/FAIL report
```

Self-evaluation scenarios live in `dogfood/scenarios/fetchcore-self.yaml`.

At least one scenario verifies `get_source`, so the gate tests bounded source serving as well as search.

## What this dogfood proves

It can expose regressions in:

- repository acquisition/scanning;
- indexing policy and fingerprints;
- Git delta discovery;
- targeted Solr persistence;
- snapshot currentness;
- retrieval over the persisted index;
- MCP process startup;
- MCP `search`;
- MCP `get_source`.

It does **not** replace the frozen retrieval baseline or retrievalgate. A passing dogfood run proves that the integrated path satisfies its reviewed scenarios; it does not prove a general retrieval-quality improvement.
