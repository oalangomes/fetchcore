# Self-hosted repository indexing

FetchCore can index a repository from GitHub Actions on a self-hosted runner while keeping indexing semantics inside FetchCore. The path below describes the Solr-specific reusable workflow; the three OPERATIONAL workflows are listed below.

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

## Operational automatic indexing by backend

The reusable `.github/workflows/repository-index.yml` remains Solr-specific for existing callers. Each OPERATIONAL backend now has an **independent GitHub Actions workflow**, with separate manual dispatch, status, concurrency and evidence artifacts:

- [Solr](../../.github/workflows/operational-index.yml), preserving the existing job and reuse-only policy;
- [OpenSearch](../../.github/workflows/operational-opensearch.yml);
- [Qdrant](../../.github/workflows/operational-qdrant.yml).

All three use the same CI-success-on-`master` trigger, which always selects `sync`.
Manual dispatch on `master` exposes `index_lifecycle: plan | sync | rebuild`,
defaulting to `sync`. OpenSearch and Qdrant reuse the existing backend-neutral
indexing lifecycle through `scripts/shared_infra.py`; each runs only against its
own OPERATIONAL resource. Running one workflow manually does not start the other two.

**Retrieval mode policy:** all three operational workflows default to `hybrid`, with `dense` as an explicit alternative. For manual execution, choose the `retrieval_mode` input in that backend's **Run workflow** dropdown. For automatic post-CI executions, set the optional repository variable `FETCHCORE_OPERATIONAL_RETRIEVAL_MODE` to `hybrid` (default when unset) or `dense`. Invalid values fail explicitly; do not silently fall back. Embedding identity stays `ollama/embeddinggemma`, 768 dimensions, regardless of the selected retrieval mode. Solr/OpenSearch hybrid uses lexical BM25+dense; Qdrant uses sparse+dense. Targeted fast paths remain capability-dependent.

This selection configures the **indexing workflow's** retrieval readiness and embedding requirements, not every long-running CLI/MCP/API consumer automatically. Configure the corresponding consumer runtime with the same `retrieval.dense.enabled` and `retrieval.hybrid.enabled` policy through the existing `FETCHCORE_CONFIG` / `FETCHCORE_RETRIEVAL` contract. Switching from a compatible hybrid vector snapshot to dense should not require re-embedding solely because of the search mode. A Solr/OpenSearch snapshot that is lexical-only cannot be promoted by `sync`; use an authorized, explicit vector `rebuild` if needed and verify fingerprints/readiness before enabling the workflow.

For the OpenSearch/Qdrant workflows, set the repository variable
`FETCHCORE_OPERATIONAL_OLLAMA_URL` to the verified, credential-free Ollama URL
**reachable from the self-hosted runner**. The runtime requires `embeddinggemma`
at 768 dimensions. Only existing services and schema-compatible resources/aliases
are accepted; `ready` and `prepare` run without creating resources. `sync` requires
an existing compatible snapshot. Manual `plan` can report absence or incompatibility;
manual `rebuild` invokes the existing explicit replacement command. No workflow
pulls a model or repairs a schema. Qdrant lexical/targeted remains unsupported.
An unavailable provider or incompatible live schema stops the run visibly.

### Manual operational recovery

Use the backend's OPERATIONAL workflow linked above, then **Run workflow** with
**Use workflow from: master**. Manual runs from other branches are rejected.

1. Select `index_lifecycle: plan` and the intended `retrieval_mode`. Inspect the
   uploaded `status-before.json` and `plan.json`: repository, target/indexed
   revision, reason, schema/projection compatibility, expected/observed
   fingerprints and any mutation preview. The workflow performs no index or
   snapshot writes for this action. Existing service/schema/provider preflight
   still runs, including one embedding probe; document vectors are not generated.
2. Verify resource ownership and decide whether replacement is authorized using
   the [snapshot recovery runbook](repository-indexing.md#missing-or-incompatible-snapshot-runbook).
   A missing resource/alias, schema failure, collision or unknown ownership is a
   stop condition. This flow does not provision or adopt resources. Correct and
   validate schema separately before proceeding.
3. After explicit authorization, dispatch **the same backend workflow** again
   with `index_lifecycle: rebuild` and the same retrieval profile and resource
   configuration. The selected `master` revision is recorded in the evidence;
   if `master` or configuration changed since inspection, inspect a new plan
   before authorizing replacement. Each run recalculates status/plan before the
   selected command. Selecting `rebuild` is the explicit replacement instruction;
   no failed `sync` or automatic CI event selects it.
4. Inspect `execution.json`, `status-after.json` and `readiness.json`. The workflow
   requires the persisted revision to match its target SHA, currentness to be
   `current`, expected/observed indexing and projection fingerprints to match,
   and requested retrieval readiness to succeed. A mismatch fails the run.

Plan runs stop after inspection; readiness after execution is not claimed for a
plan. Rebuild can still fail closed on corrupt state or adapter ownership checks.
Subsequent automatic runs continue to use strict `sync` and never recover by
rebuilding implicitly. Schema migration and serving revision reconciliation
remain separate operator actions.

### Operational acceptance (2026-10-20)

At the accepted `master` revision
[`0d7c856a1d6083b6bfb27804e15283a94c448e43`](https://github.com/oalangomes/fetchcore/commit/0d7c856a1d6083b6bfb27804e15283a94c448e43),
GitHub Actions reports successful, independent post-CI runs for all three backends:

| Backend | Successful run | Directly verified |
| --- | --- | --- |
| Solr | [39483667899](https://github.com/oalangomes/fetchcore/actions/runs/39483667899) | Existing-snapshot gate, sync, and retrieval-readiness steps succeeded. |
| OpenSearch | [39483667532](https://github.com/oalangomes/fetchcore/actions/runs/39483667532) | Existing-snapshot gate and sync/readiness verification steps succeeded. |
| Qdrant | [39483667568](https://github.com/oalangomes/fetchcore/actions/runs/39483667568) | Existing-snapshot gate and sync/readiness verification steps succeeded. |

The operator separately confirmed a completed **manual explicit rebuild** of all
three incompatible OPERATIONAL snapshots and `readiness` returning `ready: true`
for each backend. The linked runs verify the subsequent workflow path; they do
not themselves document the earlier manual rebuild. In the code,
`PROJECTION_VERSION` remains `"3"` while the internal
`_PROJECTION_CONTRACT_REVISION` changed to `"4"`; this changed the Projection
contract fingerprint and required the explicit rebuild. See the
[Projection contract](../architecture/projection.md). This is not a public
Projection schema version bump.

The accepted operating cycle is **explicit rebuild when needed → automatic
incremental sync → readiness**. OPERATIONAL resources remain isolated from DEV
and BENCH. These runs establish operational function, not equivalence of
retrieval quality, recall, latency or cost between backends.

The indexed repository is `oalangomes/fetchcore`; writers are serialized per backend, and each workflow publishes bounded JSON command evidence separately. These OPERATIONAL workflows are not BENCH runs, retrieval relevance certification or an authorization to modify unrelated DEV/BENCH namespaces.

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
uses the bundled copy of `infra/solr/configset` from an installed wheel by default, which preserves the behavior-proven analyzers, BM25
parameters and vector field contract extracted from AgentsOrchNext. The CLI never
silently rewrites an incompatible schema. `dry-run` performs no persistent writes.
With explicit dense/hybrid retrieval configuration, bootstrap also requires the
vector field, resolves the provider through application composition, and performs
one counted embedding probe to verify provider/model reachability and 768 output
dimensions. It does not download a model, generate document vectors, or write to
Solr.

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

For a vector-enabled run, the model must already exist at the operator-owned
endpoint and the initial lifecycle must be an explicit rebuild:

```yaml
      index_lifecycle: rebuild
      evaluate: true
      evaluation_modes_json: '["lexical","dense","hybrid"]'
      embedding_provider: ollama
      embedding_model: embeddinggemma
      embedding_dimensions: 768
      ollama_url: http://127.0.0.1:11434
```

The workflow passes this backend-neutral embedding contract to FetchCore. It
does not pull a model, repair schema, or implement vectorization in YAML.

## Runner selection

`runner_labels_json` is a JSON array passed directly to `runs-on`.

For FetchCore's post-merge operational index, set the repository variable:

```text
FETCHCORE_INDEX_RUNNER_LABELS_JSON
```

Example:

```json
["self-hosted","Linux","X64","local-runner","fetchcore"]
```

If the variable is absent, the operational-index workflow defaults to:

```json
["self-hosted","Linux","X64","fetchcore"]
```

The repository-specific label prevents an unrelated self-hosted runner from
claiming the operational job. Runner lifecycle remains an operator concern.
Pull-request self-dogfood runs on GitHub-hosted runners with an owned ephemeral
Solr runtime and never writes to the persistent operational resource.

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

Every persistent repository index gets its own core/collection. For example, a
second repository must not use FetchCore's operational `fetchcore` core:

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

For missing, incompatible, invalid or ambiguous snapshots, follow the
[snapshot recovery runbook](repository-indexing.md#missing-or-incompatible-snapshot-runbook):
inspect `status`/`plan`, decide with verified resource binding and schema,
authorize any rebuild separately, then verify persisted currentness and
fingerprints. Existing-snapshot OPERATIONAL jobs stop on failure; they never
turn that failure into rebuild or schema repair authorization.

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
and executed lifecycle, embedding snapshot identity, measured index/embedding
telemetry, the bounded bootstrap probe, and evaluation modes. It composes existing facts and does
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

`.github/workflows/self-dogfood.yml` runs for pull requests and can also be
dispatched manually. It checks out the candidate revision as both the target
repository and FetchCore runtime, provisions only an owned ephemeral local Solr
when the configured endpoint is unavailable, creates the disposable
`fetchcore-self-dogfood` resource explicitly, performs an explicit rebuild, and
evaluates the result through the real MCP path.

The pull-request flow is:

```text
PR candidate revision
→ GitHub-hosted runner
→ owned ephemeral Solr
→ fetchcore-self-dogfood
→ explicit rebuild
→ fetchcore-mcp
→ reviewed repository-local scenarios
→ bounded evidence
→ owned cleanup
```

The disposable resource is not an operational index and is not reused between
pull requests. Pull requests never write to the persistent `fetchcore` resource.

Self-evaluation scenarios live in `dogfood/scenarios/fetchcore-self.yaml`.
At least one scenario verifies `get_source`, so the gate exercises bounded
source serving as well as search. Pull-request self-dogfood remains lexical by
default; explicit vector runs can evaluate `lexical`, `dense`, and `hybrid`
independently through the same reusable workflow when a compatible provider and
vector snapshot are configured. Provider, schema, or snapshot failures remain
typed failures and are never reported as lexical success.

For local Compose, `docker compose up` remains lexical and does not require
Ollama. To opt in to vectors: make the model available externally, set
`FETCHCORE_RETRIEVAL` and `FETCHCORE_OLLAMA_URL`, run an explicit
`fetchcore index ... rebuild`, verify `fetchcore readiness`, then execute dense
or hybrid search. Compose never pulls the model or rebuilds automatically.

## FetchCore operational Solr index

`.github/workflows/operational-index.yml` runs only after a successful `CI`
workflow for a `master` push from this repository, or by explicit manual
dispatch. It consumes the successful CI run's immutable `head_sha` and updates
the persistent `fetchcore` resource on the repository-affine self-hosted runner.

The normal operational contract is deliberately fail-closed:

```yaml
collection: fc-operational-fetchcore-635ab7352c2850f2
create_if_missing: false
solr_provision_mode: reuse_only
require_existing_snapshot: true
index_lifecycle: sync
evaluate: false
```

An absent resource, absent snapshot, or incompatible fingerprint is not repaired
or rebuilt automatically. Initial persistent bootstrap/rebuild is an explicit
operator action through the reusable indexing workflow. Subsequent master runs
must resolve to measured incremental work or no-op when compatible.

The operational caller checks persisted snapshot presence, indexing fingerprint
and status (`ready` or `changes_pending`) after resource preflight and before
sync. A new revision can be incrementally synchronized; an absent or
incompatible snapshot fails before writing. Hosted self-dogfood leaves this
gate off because its per-run Solr container is intentionally empty.

The indexing workflow does not change an already-running MCP/API process's
revision binding. After a successful OPERATIONAL update, run the explicit
[serving reconciliation](mcp.md#reconcile-after-an-operational-index-update)
from the operator host. It validates the persisted snapshot against the
authorized workspace HEAD and only then updates the operator-owned serving
runtime and recreates the two serving containers. Index data and Solr are left
running. `readiness` and `index status` expose bounded worktree path/count
evidence when the same revision has pending changes; unknown coverage remains
not ready. Path samples are limited and sensitive names are redacted.

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
