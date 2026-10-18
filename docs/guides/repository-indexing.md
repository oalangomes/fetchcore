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
case silently invokes rebuild. `rebuild` uses canonical full indexing and the
adapter's validated projection replacement path. Solr does not create a collection
or repair an incompatible live schema. OpenSearch creates a new index generation
only for an explicit rebuild, then promotes its repository alias after validation.
Qdrant creates a versioned collection for an explicit rebuild and promotes its
repository alias only after the staged point corpus and snapshot have been
validated. Prior generations remain available for operator rollback.
Provision the backend service with the [runtime setup](self-hosted-indexing.md).

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
`FETCHCORE_INDEX_BACKEND` and `FETCHCORE_INDEX_BACKEND_SETTINGS` support. Solr and
OpenSearch and Qdrant support the registered operational lifecycle through the
shared operational driver. Qdrant requires an explicit positive
`vector_dimensions` setting for operational indexing; the configured value must
match both the collection schema and the Ollama provider output.

For OpenSearch, pass the endpoint and index namespace explicitly:

```bash
fetchcore index --backend opensearch \
  --backend-settings '{"url":"http://127.0.0.1:9200","index":"fetchcore-knowledge"}' \
  plan owner/project --json
```

`bootstrap` is a read-only check of the bound alias and Projection v3 mapping.
A missing alias returns `index_missing`; `plan` reports `initial`. Explicit
`rebuild` creates a fresh generation and atomically moves the alias. The prior
generation remains available for operator rollback. A failed write before
promotion leaves the prior generation active; an ambiguous promotion keeps
staging for inspection.

OpenSearch `sync` applies a canonical document delta to a staged generation and
promotes the alias after checking its snapshot, document count, and corpus
checksum. Unchanged documents are not re-embedded or sent by FetchCore. OpenSearch
copies the active generation inside the server, so storage writes and temporary
disk use scale with the existing corpus on each sync. `backend_writes` includes
that copy, changed documents, and the snapshot. A per-repository writer lock is
an index named `<repository-alias>-write-lock`. A failed process may leave it
behind. Inspect its mapping `_meta.fetchcore_lock_token` and the active alias
before any manual recovery. `plan` and `status` never acquire the lock or write.
The [2026-10-17 operational preflight](../benchmarks/results/opensearch-operational-preflight-2026-10-17.md)
records the verified Ollama route, explicit initial OpenSearch rebuild, and
post-rebuild operational checks. It is not a comparative quality benchmark.

Qdrant `sync` uses the same explicit full-scan fallback policy for unsafe or
unavailable Git deltas, but never turns that scan into a projection rebuild.
It stages a new generation, copies existing vectors without re-embedding
unchanged documents, applies stable-ID upserts/deletes, verifies document count
and corpus checksum, then promotes the alias. Dense and hybrid searches pin the
concrete generation selected by the snapshot, so in-flight reads remain on the
old immutable generation across promotion. Previous generations are retained;
orphan cleanup is a separate, explicit maintenance operation. `sync` on an
absent or incompatible snapshot fails and requires an explicit rebuild.

Qdrant serializes rebuild/sync writers with a repository-scoped lock collection
in Qdrant, shared across processes and hosts using the same endpoint. A competing
writer receives `concurrent_write`. If a process terminates before releasing its
lock, FetchCore fails closed and reports the lock collection for operator
inspection; it does not automatically delete a possibly live lock. No-op avoids
the writer lock and performs no point, snapshot, collection, or embedding writes.
Qdrant retrieval capabilities remain dense and hybrid; targeted/exact lookup is
not advertised by this backend.
The [2026-10-18 Qdrant operational acceptance](../benchmarks/results/qdrant-operational-acceptance-2026-10-18.md)
records a real OPERATIONAL rebuild, same-revision no-op, committed Git delta
with add/update/delete/rename, dense/hybrid search, source retrieval, and P0
generation/lock checks. It is a lifecycle gate, not a ranking-quality benchmark.

Lexical indexing is the default and never resolves or calls an embedding
provider. Dense and hybrid indexing are explicit opt-ins through the shared
operator runtime contract. The same `provider`, `model`, `dimensions`, and
serializer/projection identity are used later for query embedding. For the
current Solr and OpenSearch vector schemas, `dimensions` must be `768`; a different configured or
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

Solr commands retain their original JSON envelope and legacy initial/recovery
rebuild behavior. OpenSearch legacy `bootstrap` validates its existing alias;
its `sync` requires a compatible snapshot. Their environment variables still work. Explicit legacy
`--workspace`, `--repository`, `--branch`, or `--revision` cannot be combined with
a registered positional target. Legacy identity environment variables are ignored
when a positional target is supplied; the registry/provider binding supplies all
identity information. No second repository catalog is introduced.

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
On Solr, an existing compatible snapshot additionally exposes optional observed
`stage_timings_ms` measurements under the registered `plan` result and the `sync`
`execution` result. `snapshot_load_ms` times the snapshot read within the plan or
sync step; `previous_documents_load_ms` times loading and decoding the existing
canonical corpus; `canonical_delta_index_ms` times the chosen incremental delta
indexing/compare path (including an explicit full-scan fallback when required);
`persistence_call_ms` times the adapter call to persist and validate mutations
and is available only for a sync that actually writes. The existing
`execution.duration_ms` remains the backend adapter's own measurement and need not
match `persistence_call_ms`. These are monotonic *observations* and are not
predictions or complete CLI/Actions step durations: materialization, repeated
status/readiness checks, separate Solr calls and process startup are outside the
reported spans. No-op only includes the snapshot read; absent stages are
unmeasured/not applicable, **not** elapsed zero. Full-scan/rebuild-only branches
are not claimed as fully instrumented. Incompatible projection states still fail
closed. This diagnostic does not change index/projection/serializer fingerprints.

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
