# Index registered repositories

Register once, then address the same canonical identity or unambiguous leaf name:

```bash
fetchcore repo add .
fetchcore index plan fetchcore --json
fetchcore index status fetchcore --json
fetchcore index rebuild fetchcore --json
fetchcore index sync fetchcore --json
```

`plan` reports `initial` when no snapshot exists. Choose `rebuild` to create the
first index. `sync` requires an existing compatible index; absence returns
`snapshot_missing`. An incompatible index returns `rebuild_required`. Neither
case silently invokes rebuild. `rebuild` uses the existing canonical full indexing
and transactional projection replacement/validation path. It does not create a
collection or repair an incompatible live schema. Provision the backend with the
existing [runtime setup](self-hosted-indexing.md) first.

## Configuration and compatibility

Group options precede the subcommand:

```bash
fetchcore index --cache-root /srv/fetchcore-cache --allowed-root /srv/repos \
  --config /etc/fetchcore/runtime.yaml \
  --backend solr \
  --backend-settings '{"url":"http://127.0.0.1:8983/solr","collection":"repo-knowledge"}' \
  plan owner/project --json
```

Cache precedence and local source authorization are identical to
[repo inspect](repository-registry.md). Backend settings retain
`FETCHCORE_INDEX_BACKEND` and `FETCHCORE_INDEX_BACKEND_SETTINGS` support. Solr is
currently the only operational backend with both incremental and rebuild
capabilities in this surface. Other backend IDs fail with `unsupported_backend`;
retrieval support alone does not imply operational persistence support.

Lexical indexing is the default and never resolves or calls an embedding
provider. Dense and hybrid indexing are explicit opt-ins through the shared
operator runtime contract. The same `provider`, `model`, `dimensions`, and
serializer/projection identity are used later for query embedding. For the
current Solr schema, `dimensions` must be `768`; a different configured or
observed dimension fails with `schema_mismatch`.

Enabling dense/hybrid over an existing lexical snapshot yields
`rebuild_required`. Run `fetchcore index ... rebuild` explicitly; `sync` never
silently vectorizes the existing corpus. Provider/model/dimension or serializer
identity changes have the same explicit rebuild requirement.

Repository-owned `.fetchcore/indexing.yaml` can bound Markdown units independently
of code and configuration chunks:

```yaml
version: 1
chunking:
  max_lines: 160
  overlap_lines: 20
  markdown_max_chars: 48000
```

The Markdown character limit and section-unit contract participate in the
indexing fingerprint. Updating either may produce `rebuild_required`; review the
plan and use the explicit rebuild operation rather than treating `sync` as a
migration.

The #60 commands remain compatible:

```bash
fetchcore index --workspace . --repository owner/project --revision COMMIT status
fetchcore index --workspace . --repository owner/project --revision COMMIT dry-run
fetchcore index --workspace . --repository owner/project --revision COMMIT sync
fetchcore index --workspace . --repository owner/project bootstrap
```

Those commands retain their original JSON envelope and legacy initial/recovery
rebuild behavior. Their environment variables still work. Explicit legacy
`--workspace`, `--repository`, `--branch`, or `--revision` cannot be combined with
a registered positional target. Legacy identity environment variables are ignored
when a positional target is supplied; the registry/provider binding supplies all
identity information. No aliases or second catalog are introduced.

## Observation, plan, and execution

`RepositoryIndexApplicationService` resolves through
`RepositoryApplicationService.inspect()` → `RegisteredRepository.source_request()`
→ canonical provider → existing runtime. The CLI only routes and renders.

Local workspaces re-observe branch/HEAD on every operation. Git registrations
remain pinned to the registered revision; they materialize a temporary worktree
from the existing validated mirror with `refresh=False`, without fetch, clone,
registry updates, or repair. Normal context cleanup removes that worktree, including
on operation failures. Missing source/mirror fails explicitly. Repository identity,
branch, revision and source reference are passed to the canonical indexer; the
temporary directory is never used as repository identity.

`plan` and `status` make no persistent backend or registry writes. Planning never
calls sync, rebuild, snapshot updates, embeddings, or collection provisioning. Git
temporary worktree bookkeeping is the only materialization exception. Planning
uses the existing canonical scanner/delta preview and may read persisted documents.
Plans do not reserve a revision or lock the backend. Serialize all writers sharing
a collection: the existing Solr commit/rollback scope is collection-wide, including
when writers target different repositories. Execution rechecks snapshot
compatibility and disables the synchronizer's implicit rebuild policy.

`status` reports `current`, `stale`, `absent`, `incompatible`, or `unknown`.
`current` requires compatible persisted state plus a clean same-revision Git delta,
not mere presence of a workspace/mirror. Unknown local revisions remain unknown.
Cheap status also reports unknown for dirty same-HEAD workspaces: Git differences
from HEAD alone do not prove differences from indexed content. Plan performs the
canonical comparison and can prove `canonical_noop` for already-indexed dirty
content. Dirty workspaces follow the existing Git delta/content-checksum contract;
a matching HEAD alone does not imply no-op or an immutable workspace snapshot.

`plan` modes are `initial`, `incremental`, `noop`, and `rebuild_required`. A full
canonical **scan** may be necessary for document changes or unavailable Git history;
it remains distinct from full projection **replacement**. The existing runtime
reports that discovery reason and applies a canonical document diff. No-op sync
performs zero backend writes, including snapshot writes.

## Output

Targeted commands support human output or `--json`, representing identical facts:

```json
{"operation":"plan","status":"succeeded","result":{}}
```

The result contains `repository`, `provider`, `source`, `source_ref`, `branch`,
`registered_revision`, `target_revision`, `indexed_revision`, `mode`, `currentness`,
`reason`, `incremental_possible`, `schema_compatible`, `projection_compatible`,
`indexing_fingerprint`, and `projection_fingerprint`. Null means unavailable, not
compatible/current. Incompatibility can prevent decoding the indexed revision.
`projection_compatible` concerns persisted projection serialization; an indexing
policy or embedding identity mismatch is separately exposed by `reason`.

Compatible plans also include the existing `planned_mutations`, `discovery`, and
available `indexing_telemetry` (scan/chunk counts). The optional
`discovery.discovery_duration_ms` measures the complete Git change-detection step;
`discovery.git_diff_duration_ms` measures the accumulated execution/parsing time of
the committed-revision and working-tree `git diff` commands. Both use a monotonic
clock, are measured in milliseconds, and are exposed in `plan`, `status`, and
`sync` discovery observations. The `git_diff` field is omitted if no successful
Git delta could be established (including full-scan fallback); an absent field is
**unmeasured**, not zero. These numbers are not total plan/sync durations, and
they do not include canonical document comparison, materialization, or persistence.
Full-scan discovery placeholder counts are omitted rather than presented as
measured zero changes. With an absent snapshot, prior document
and delete counts are null because orphan backend records cannot be ruled out.
Counts describe canonical documents, not backend HTTP requests. In vector mode,
execution telemetry reports measured embedding requests/vectors/duration and
backend writes/deletes. No-op reports observed zeros. Plans still omit
unmeasured vector work and FetchCore does not implement a cross-rebuild embedding
cache here.

Execution returns `mode: rebuild|incremental|noop` and `execution`, with mutation
counts, measured execution telemetry where available, and a normalized `snapshot`
containing repository/branch/revision, count, indexed time, status, fingerprints,
and embedding/serializer identity when available. `previous_indexed_revision` and
`previous_currentness` explicitly retain observations made before execution; call
`status` to re-observe the source afterward.

Failures exit 1 with
`{"operation":"sync","status":"failed","error":{"category":"...","message":"..."}}`.
Registry categories are reused, including `repository_not_found`,
`ambiguous_repository`, `missing_materialization`, and `materialization_failure`.
Indexing categories include `unsupported_backend`, `incremental_unsupported`,
`snapshot_missing`, `rebuild_required`, `schema_mismatch`, and adapter failure
categories such as `index_update_failure`. Raw backend error messages and settings
are not printed. Syntax errors retain Click's usage-error exit 2.
