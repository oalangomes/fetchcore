# Self-hosted repository indexing

FetchCore can index a repository from GitHub Actions on a self-hosted runner while keeping indexing semantics inside FetchCore.

```text
GitHub Actions
    ↓
fetchcore index bootstrap / status / plan / sync / rebuild
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

Every successful command emits JSON. `bootstrap` validates the active Solr
resource against the FetchCore Projection v3 schema. Explicit owned provisioning
uses `infra/solr/configset`, which preserves the behavior-proven analyzers, BM25
parameters and vector field contract extracted from AgentsOrchNext. The CLI never
silently rewrites an incompatible schema. `dry-run` performs no persistent writes.

Equivalent environment variables are available:

- `FETCHCORE_INDEX_WORKSPACE`;
- `FETCHCORE_INDEX_REPOSITORY`;
- `FETCHCORE_INDEX_BRANCH`;
- `FETCHCORE_INDEX_REVISION`;
- `FETCHCORE_INDEX_BACKEND`;
- `FETCHCORE_INDEX_BACKEND_SETTINGS`.

The repository-targeted [indexing lifecycle](repository-indexing.md) is the
workflow execution surface. The legacy explicit workspace/identity surface remains
available for compatibility, but the workflow registers its checked-out workspace
and uses strict `plan <repo>`, `sync <repo>`, and `rebuild <repo>` commands.
Registered `sync <repo>` requires an existing compatible index and never inherits
legacy recovery behavior.

## Reusable workflow

The repository provides `.github/workflows/repository-index.yml` as a reusable workflow and manual workflow.

An adopting repository can call a healthy existing Solr service:

```yaml
jobs:
  index:
    uses: oalangomes/fetchcore/.github/workflows/repository-index.yml@master
    with:
      runner_labels_json: '["self-hosted","Linux","X64"]'
      target_repository: ${{ github.repository }}
      target_ref: ${{ github.sha }}
      target_branch: ${{ github.ref_name }}
      fetchcore_ref: master
      solr_url: ${{ vars.FETCHCORE_SOLR_URL || 'http://127.0.0.1:8983/solr' }}
      solr_deployment_mode: auto
      collection: my-project-knowledge
      create_if_missing: false
      solr_provision_mode: reuse_only
      index_lifecycle: sync
      evaluate: false
      evaluation_modes_json: '["lexical"]'
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
["self-hosted","Linux","X64","fetchcore"]
```

The repository-specific label prevents an unrelated self-hosted runner from
claiming the job. Runner lifecycle remains an operator concern: on-demand
runners must be made available before a manual dispatch, while continuous
post-merge dogfood requires an explicitly configured always-on runner. Pull
request code is validated on GitHub-hosted runners and is not sent to this
self-hosted path.

## Reuse-first Solr lifecycle

The safe default is `solr_provision_mode: reuse_only`. The workflow probes the
configured URL's System Information API and validates the response as Solr; an
open TCP port alone is not readiness. It distinguishes connection refusal,
timeout, authentication denial, server errors, a non-Solr HTTP endpoint, and an
unknown or mismatched deployment mode. Endpoint diagnostics never include URL
credentials; credential-bearing URLs are rejected.

Deployment mode selects the resource API explicitly:

```text
standalone (`mode=std`) → CoreAdmin → dedicated core
SolrCloud               → Collections API → dedicated collection
```

An existing compatible service is always reused. That path performs no Docker
start, stop, restart, volume, or cleanup operation. Resource existence is checked
separately from service health. Existing resources are never recreated or have
their schema replaced. FetchCore's public `index bootstrap` validates Projection
v3 compatibility, while `index plan/status` validate snapshot identity,
fingerprints, repository binding, and currentness.

### Creating a missing core or collection

Creation requires `create_if_missing: true`. It creates only `collection` and uses
the named, server-side `solr_configset` (default `fetchcore`). An external Solr
operator must install that configset first. If it is unavailable, creation fails
explicitly; the workflow does not upload, repair, or replace a configset.

For an initial dedicated resource, manually dispatch
`.github/workflows/repository-index.yml` as a separate initialization run. Set
`collection` to the repository's dedicated resource, keep
`solr_provision_mode: reuse_only`, and explicitly set:

```yaml
create_if_missing: true
index_lifecycle: rebuild
```

This authorizes creation of only that resource using the already-installed
`fetchcore` configset, followed by the explicitly requested initial rebuild.
The operation is idempotent: if the resource already exists, it is reused and
is not recreated. Schema and repository-binding incompatibilities remain
fail-closed. If no initial indexing is needed, select `plan` instead.

After that succeeds, change the normal workflow to:

```yaml
create_if_missing: false
index_lifecycle: sync
```

The second and later runs preserve `noop` and incremental behavior. `sync` never
falls back to rebuilding: an absent snapshot fails with `snapshot_missing`, and
an incompatible snapshot fails with `rebuild_required`.

### Optional owned provisioning

If no service exists at a loopback HTTP endpoint, provisioning is available only
with both explicit authorizations:

```yaml
solr_provision_mode: provision_if_missing
create_if_missing: true
```

That path selects a free loopback port, creates a uniquely named ephemeral
container from the digest-pinned Solr image, and records it as owned by the run.
Cleanup removes only that exact owned container. It never deletes a pre-existing
container or volume. `managed_solr: true` remains as a deprecated compatibility
alias for this opt-in policy. Nonempty `solr_port` and `solr_volume` inputs fail
explicitly: configure `solr_url` for persistent service ownership. Fixed ports
and shared-volume ownership are unsupported.

## Isolating a second repository

Every repository gets its own core/collection. For example, a second repository
must not use FetchCore's `fetchcore-self-dogfood` core:

```yaml
jobs:
  widgets-index:
    uses: oalangomes/fetchcore/.github/workflows/repository-index.yml@master
    with:
      target_repository: acme/widgets
      target_ref: ${{ github.sha }}
      target_branch: main
      solr_url: ${{ vars.FETCHCORE_SOLR_URL }}
      collection: acme-widgets-knowledge
      solr_provision_mode: reuse_only
      create_if_missing: false
      index_lifecycle: sync
```

This proves operational isolation, not federated retrieval. A resource containing
documents belonging to a different repository, or documents without repository
identity, fails with `repository_resource_collision`; the resource is never silently
repurposed.

## Explicit index lifecycle

The reusable input `index_lifecycle` is one of `plan`, `sync`, or `rebuild`. The
workflow always runs:

```text
Solr preflight
→ schema bootstrap/validation
→ status
→ plan
→ requested sync or explicit rebuild (unless plan-only)
→ status
→ optional evaluation
```

The registered repository application service owns `initial`, `incremental`,
`noop`, and `rebuild_required` semantics. YAML selects an explicit public command;
it does not import adapters or implement a second indexer.

For a persistent compatible snapshot:

```text
workflow run N, explicit rebuild
    ↓
Solr snapshot revision N

workflow run N+1
    ↓
same dedicated resource
    ↓
Git delta N..N+1
    ↓
targeted incremental persistence
```

## Workflow evidence

Each run executes:

```text
service/resource preflight
→ explicit schema bootstrap/validation
→ status before
→ plan
→ requested lifecycle
→ status after
```

Every piped command enables `pipefail`, so report capture with `tee` cannot turn a failed FetchCore command into a successful workflow step.

The bounded `run-evidence.json` records repository, revision, sanitized Solr
endpoint, deployment mode, reused/provisioned instance, existing/created resource,
repository binding, schema compatibility, observed snapshot/currentness, requested
and executed lifecycle, and evaluation modes. It composes existing facts and does
not anticipate the generic telemetry platform tracked by #137.

The Solr preflight also writes `solr-preflight.json` before returning a known
failure, including `resource_missing`, service connectivity/authentication
failures, and repository-binding collisions. The artifact is bounded and omits
credential-bearing URLs. A missing resource is reported separately from an
unavailable or incompatible Solr service.

Writers are serialized by core/collection identity within the caller repository's
GitHub Actions concurrency scope. GitHub does not provide a cross-repository global
concurrency group. Cross-repository callers must therefore use distinct resources;
the workflow does not pretend to provide a distributed lock.

## FetchCore self-dogfood

`.github/workflows/self-dogfood.yml` runs only after the `CI` workflow succeeds
for `master`, and can also be dispatched manually. A failed or cancelled CI run
does not enqueue self-dogfood. Only a `push` run originating from the FetchCore
repository is accepted; pull request and fork revisions cannot reach the
self-hosted job through this trigger. The workflow indexes the exact successful
CI `head_sha`, rather than a later moving branch head.

It indexes `oalangomes/fetchcore` into the dedicated
`fetchcore-self-dogfood` core/collection, reuses the configured Solr service, and
then evaluates the index through the real MCP stdio server. The normal wrapper
requests `sync` with `create_if_missing: false`; initialization remains a separate
explicit `create_if_missing: true` and `index_lifecycle: rebuild` dispatch. If the
dedicated resource is absent, the normal run fails with `resource_missing`.

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

The operational evaluation mode is currently `lexical`. Requests for `dense` or
`hybrid` fail validation explicitly and identify #130 as the missing vector-ready
index/provider contract. They are never reported as lexical success. A schema that
contains a vector field is not evidence that vectors were indexed.

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
