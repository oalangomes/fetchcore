# CLI and MCP reference

| Entry point | Current operation |
| --- | --- |
| `fetchcore doctor` | Read-only operational checks for the resolved runtime. |
| `fetchcore capabilities` | Show typed retrieval, intent-translation, and indexing capabilities. |
| `fetchcore config show` | Show resolved operator runtime configuration and known source layers. |
| `fetchcore repo add <source>` | Materialize and persist a canonical repository registration; repeated add does not refresh. |
| `fetchcore repo list` | Read known registrations in canonical identity order. |
| `fetchcore repo inspect <repo>` | Resolve a canonical identity or unique leaf name and inspect registration facts. |
| `fetchcore index --workspace <path> corpus` | Inspect local canonical safe-scan coverage without registration or a backend. |
| `fetchcore index plan <repo>` | Read-only registered-source planning. |
| `fetchcore index status <repo>` | Observe registered, materialized and indexed revision/currentness. |
| `fetchcore index sync <repo>` | Incremental synchronization or no-op; never implicit rebuild. |
| `fetchcore index rebuild <repo>` | Explicit canonical full indexing and projection replacement. |
| `fetchcore search "<query>" --repo <repo>` | Bounded repository-scoped retrieval through the application service. |
| `fetchcore source <source-ref>` | Bounded, authorized source read from a search result reference. |
| `fetchcore find-symbol <name> --repo <repo>` | Exact declaration lookup by name, qualified name or canonical identity. |
| `fetchcore get-related <identity-or-source-ref> --repo <repo>` | Bounded outgoing structural observations with explicit coverage. |
| `fetchcore index graph-rebuild <repo>` | Explicitly build and activate a full graph generation for the current knowledge snapshot. |
| `fetchcore index graph-status <repo>` | Inspect graph readiness and retained generations without mutation. |
| `fetchcore navigate-graph <canonical-symbol-id> --repo <repo>` | Compose a bounded map of resolved relations and evidence, up to three hops. |
| `fetchcore index bootstrap` | Validate the Solr schema contract. |
| `fetchcore index status` | Report snapshot compatibility and repository freshness. |
| `fetchcore index dry-run` | Preview indexing changes without persistent writes. |
| `fetchcore index sync` | Synchronize repository knowledge to Solr. |
| `fetchcore-mcp` | Start the MCP stdio server. |
| MCP `search` | Bounded repository-scoped `KnowledgeSearchResponse`. |
| MCP `get_source` | Bounded authorized source read. |
| MCP `find_symbol` | Exact canonical symbol lookup with extraction coverage. |
| MCP `get_related` | Directed observed/resolved relationships, without graph inference. |
| MCP `navigate_graph` | Optional bounded structural map from an exact canonical symbol seed. |

All `repo` commands support `--json`; human and JSON output contain the same
registration and materialization facts. Group options `--cache-root` and
repeatable `--allowed-root` precede the subcommand; `add` accepts an optional
`--provider local-workspace|git`. See the [repository registry guide](../guides/repository-registry.md)
for identity, persistence, source safety, output fields, and failure categories.

`search` requires `--repo` and accepts `--limit` (1..100), repeatable
`--knowledge-type`, and `--json`. `source` accepts `--repo` for a relative `source_ref`, optional
`--revision`, `--checksum`, `--line-start`, `--line-end`, `--context-lines`, and `--json`.
Pass the checksum returned by `find_symbol` to `source --checksum` or MCP
`get_source(checksum=...)` to reject source bytes changed since extraction.
Repository path and knowledge-type bindings also constrain source reads; a
symlink cannot escape an authorized path prefix. Materialized navigation rejects
snapshots whose document count or canonical corpus checksum does not match.
`--revision` is a precondition against the revision actually materialized; a
mismatch fails with `source_revision_mismatch` and does not fetch or switch to
the requested revision. A local workspace is never mutated. Git registrations
remain pinned and use a temporary cached worktree without refresh; mutable
local workspaces are only observed at their current revision. Both commands use
the operator runtime configuration from #65 for backend/retrieval composition
and the canonical repository registry from #68 for identity and source
materialization. A conflicting configured repository or revision fails
explicitly. Neither command indexes or synchronizes a repository. Source reads
remain path-safe and bounded by the serving limits. A mutable local workspace
reports its observed current revision; registered Git repositories are read at
their pinned revision through a temporary cached worktree without fetching.

## Exact structural navigation

`find-symbol` and `get-related` share `--repo`, `--revision`, `--limit` (1..100),
`--config`, `--cache-root`, repeatable `--allowed-root`, and `--json`.
`find-symbol` also accepts `--language`, `--kind`, and exact `--path`.
`get-related` accepts repeatable `--relation-type`, `--direction outgoing|incoming`,
`--depth 1`, and `--require-resolution`. Unsupported directions, depths and
relation types fail explicitly. Supported filters are `calls`, `references`,
`tests`, `imports`, `dependencies`, `extends`, and `implements`.

These commands read the active compatible operational snapshot and bounded
canonical candidates from persisted exact lookup fields. Index explicitly with `fetchcore index rebuild <repo>` or
`fetchcore index sync <repo>` before navigation; an absent or incompatible snapshot
fails without rebuilding during retrieval. The indexer derives the lookup fields
from canonical declarations when publishing a snapshot; navigation checks its
fingerprint and revision, then hydrates only bounded candidates. It does not
parse source files, embed, invoke textual search or require a graph database.
The exact lookup fields are indexed in Solr, OpenSearch and Qdrant. A candidate
budget exhaustion is reported explicitly. Repository path and
knowledge-type binding restrictions apply before navigation; target location
fields outside that scope are withheld. `get_source` checks knowledge-type scope
through an exact `source_ref` lookup. `--revision` is an exact precondition,
never a request to change HEAD.

The SDK exposes `FindSymbolRequest`, `GetRelatedRequest`, and `NavigationResult`
from `fetchcore.application`. Pass these to `find_symbol` / `get_related` on
`RetrievalApplicationService` or `RepositoryRetrievalApplicationService`.
The existing `RepositoryKnowledgeNavigationService` still supports callers
that already hold canonical documents.

Results preserve identity, repository, revision, path/span, checksum and producer
provenance. Overloads and homonyms remain distinct, deterministically ordered
candidates. Select a returned canonical `identity` for symbol-owned relations;
use its `source_ref` for file-owned imports/dependencies. No string-name matching
is used to invent call destinations. Source hydration remains the existing
bounded `source` / `get_source` operation.

Both operations return:

- `results`: at most `limit` typed symbol definitions or directed relations;
- `coverage`: per-capability source counts, producer declarations and
  `supported`, `partial`, `unsupported`, or `unknown` coverage;
- `status`: `success` or `not_found` only under supported coverage; otherwise
  `partial`, `unsupported`, or `unknown`, including for empty results;
- `limit_reached`: the returned count reached the requested bound, so more
  candidates may exist; this is not a claim that an additional candidate exists;
- `scan_complete` and `scan_diagnostics`: canonical scan blockers, including
  budget truncation. An incomplete scan always makes the result partial.

Coverage describes the admitted canonical corpus, not every file in the
repository. Empty scopes are unknown. Missing extraction does not become a
structural `not_found` or trigger textual/dense search. `require_resolution`
reports unavailable cross-file resolution explicitly and does not return
unresolved observations as if resolved. Incoming traversal requires a canonical
symbol identity and includes only edges with a uniquely resolved target. Other
languages and relation families retain their observed or unsupported status.
Multi-hop navigation is a separate optional GraphProjection capability; the
existing `get-related` contract and results are unchanged.

## Optional graph knowledge map

After a compatible SolrCloud knowledge snapshot exists, explicitly run
`fetchcore index graph-rebuild <repo>`. It derives the graph from canonical
knowledge and publishes a separate generation. `graph-status` reports whether
the active graph is still bound to the exact knowledge generation. A later
knowledge-only sync makes graph navigation unavailable until another graph
rebuild; lexical and one-hop retrieval remain available. Neither navigation
nor status rebuilds an index. The graph configset is `fetchcore-graph-v1`.

`navigate-graph` and MCP `navigate_graph` accept an exact
`symbol:v1:sha256:<64 hex>` seed, `repository`, optional `revision`, direction,
relation-type filters, `depth` (1–3), `max_nodes`, `max_edges`, `max_paths`,
`expansion_budget`, `timeout_seconds` and `max_output_bytes`. The SDK request
is `NavigateGraphRequest`; `RetrievalApplicationService.navigate_graph` returns
`GraphNavigationResult`. Text names must first be resolved by `find_symbol`;
ambiguous names are never selected silently. Each returned path names all of
its individual edge IDs; every edge carries source and target provenance.
The response also contains hydrated entities, inconclusive observations from
the canonical corpus, extraction coverage, examined counts, consumed backend
queries, diagnostics, and a `success`, `partial`, or `not_found` status.
`fetchcore capabilities` advertises the declared `graph.bounded_navigation`
flag for SolrCloud; `index graph-status` checks live readiness for a repository.

```bash
fetchcore index graph-status owner/repo --json
fetchcore index graph-rebuild owner/repo --json
fetchcore navigate-graph 'symbol:v1:sha256:<64-hex-digits>' --repo owner/repo \
  --revision <indexed-revision> --relation-type calls --depth 3 \
  --max-nodes 30 --max-edges 60 --max-paths 60 --expansion-budget 30 --json
```

The map is source-derived static evidence. A call edge does not prove runtime
execution or a complete business flow. The extractor currently supplies
declarations, imports and observed/resolved code relationships; endpoint,
persistence and event-flow links are available only where canonical extraction
provides exact evidence. An unsupported backend, absent graph, stale
generation, changed schema or inconsistent source returns a typed error; it
does not fall back to lexical search. Budget truncation and partial extraction
are explicit. See [GraphProjection v1](../architecture/graph-projection-v1.md)
for publication and recovery details.

Example (after `fetchcore repo add .` and explicit `fetchcore index rebuild <repo>`):

```bash
fetchcore find-symbol 'catalog.Client.Fetch' --repo owner/repo --language go --json
fetchcore get-related 'repository://owner/repo/client.go' --repo owner/repo \
  --relation-type imports --json
fetchcore source 'repository://owner/repo/client.go' --repo owner/repo \
  --revision <returned-revision> --line-start <returned-start> --line-end <returned-end>
```

`doctor`, `capabilities`, and `config show` accept `--json` and an optional
`--config <path>`. Doctor checks resolve the selected backend/provider and use
only available non-mutating probes. Solr schema validation, OpenSearch cluster
health, and Qdrant collection listing are read-only; no command bootstraps,
indexes, refreshes repositories, or persists embeddings. Qdrant's canonical
health probe also executes a transient embedding and validates its vector
shape. Embedding provider resolution is required when dense retrieval is
configured and for Qdrant's implicit dense runtime when retrieval configuration
is absent. A healthy result does not claim schema compatibility where the
selected adapter has no safe schema probe. Doctor exits 1 for failed
operational checks; Click usage errors retain exit code 2.

Capabilities are read from the selected adapter's declarations and the
operational `IndexingCapabilities` boundary. Retrieval support does not imply
operational indexing support: Solr, OpenSearch and Qdrant currently advertise
incremental and rebuild capabilities for registered repositories. `config show`
uses the #65 runtime loader's final resolved values and source tracking; it
masks credentials and does not inspect repository-owned `.fetchcore/*` policy.

The legacy index command takes `--workspace`, required `--repository`, optional `--branch`/`--revision`, `--backend` (currently `solr`), and JSON `--backend-settings`; matching `FETCHCORE_INDEX_*` variables are accepted. MCP runtime settings use [operator configuration](configuration.md). MCP has one authorized repository binding per process today. See the [indexing guide](../guides/self-hosted-indexing.md) and [MCP guide](../guides/mcp.md).

Registered targets use the same `--cache-root` / `--allowed-root` options as
`repo`, plus operational backend settings. Each targeted command accepts `--json`;
without a target, legacy CI commands still always emit their original JSON envelope.
See [repository indexing](../guides/repository-indexing.md) for initial indexing,
compatibility, output, and the distinction between the two sync contracts.

`index corpus --json` reports the effective repository policy, KnowledgePacks,
extractor capabilities, fingerprint, bounded scan audit and source-kind coverage.
`--path <relative-path>` tests one path's admission with the same scanner.
`--proposal <external-yaml>` validates a policy through the canonical loader and
compares observed paths read before and after; it does not edit repository policy.
Path impact is incomplete if either scan truncates and says nothing about future
paths. Exclusions take precedence over includes; basename includes also match
nested files. Retain broad protections when proposing exceptions the current
contract cannot express safely. Non-mapping policy values such as `false`, `0`
and `[]` fail closed rather than becoming defaults; empty/null YAML retains its
existing empty-policy meaning. `--deep` runs canonical
extraction in memory to report document/source coverage and exact-source
duplication diagnostics; it can cost substantially more than a scan and cannot
be combined with `--path` or `--proposal`. None of these modes registers a
repository, writes an index, embeds, or contacts a backend. A safe scan's
eligible/read counts do not prove document extraction or retrieval quality.
