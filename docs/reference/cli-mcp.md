# CLI and MCP reference

| Entry point | Current operation |
| --- | --- |
| `fetchcore repo add <source>` | Materialize and persist a canonical repository registration; repeated add does not refresh. |
| `fetchcore repo list` | Read known registrations in canonical identity order. |
| `fetchcore repo inspect <repo>` | Resolve a canonical identity or unique leaf name and inspect registration facts. |
| `fetchcore index plan <repo>` | Read-only registered-source planning. |
| `fetchcore index status <repo>` | Observe registered, materialized and indexed revision/currentness. |
| `fetchcore index sync <repo>` | Incremental synchronization or no-op; never implicit rebuild. |
| `fetchcore index rebuild <repo>` | Explicit canonical full indexing and projection replacement. |
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

The legacy index command takes `--workspace`, required `--repository`, optional `--branch`/`--revision`, `--backend` (currently `solr`), and JSON `--backend-settings`; matching `FETCHCORE_INDEX_*` variables are accepted. MCP runtime settings use [operator configuration](configuration.md). MCP has one authorized repository binding per process today. See the [indexing guide](../guides/self-hosted-indexing.md) and [MCP guide](../guides/mcp.md).

Registered targets use the same `--cache-root` / `--allowed-root` options as
`repo`, plus operational backend settings. Each targeted command accepts `--json`;
without a target, legacy CI commands still always emit their original JSON envelope.
See [repository indexing](../guides/repository-indexing.md) for initial indexing,
compatibility, output, and the distinction between the two sync contracts.
