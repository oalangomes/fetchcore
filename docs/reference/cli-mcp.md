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
| `fetchcore index bootstrap` | Validate the Solr schema contract. |
| `fetchcore index status` | Report snapshot compatibility and repository freshness. |
| `fetchcore index dry-run` | Preview indexing changes without persistent writes. |
| `fetchcore index sync` | Synchronize repository knowledge to Solr. |
| `fetchcore-mcp` | Start the MCP stdio server. |
| MCP `search` | Bounded repository-scoped `KnowledgeSearchResponse`. |
| MCP `get_source` | Bounded authorized source read. |

All `repo` commands support `--json`; human and JSON output contain the same
registration and materialization facts. Group options `--cache-root` and
repeatable `--allowed-root` precede the subcommand; `add` accepts an optional
`--provider local-workspace|git`. See the [repository registry guide](../guides/repository-registry.md)
for identity, persistence, source safety, output fields, and failure categories.

`search` requires `--repo` and accepts `--limit` (1..100), repeatable
`--knowledge-type`, and `--json`. `source` accepts `--repo` for a relative `source_ref`, optional
`--revision`, `--line-start`, `--line-end`, `--context-lines`, and `--json`.
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
