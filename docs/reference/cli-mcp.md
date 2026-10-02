# CLI and MCP reference

| Entry point | Current operation |
| --- | --- |
| `fetchcore index bootstrap` | Provision and validate the Solr schema contract. |
| `fetchcore index status` | Report snapshot compatibility and repository freshness. |
| `fetchcore index dry-run` | Preview indexing changes without persistent writes. |
| `fetchcore index sync` | Synchronize repository knowledge to Solr. |
| `fetchcore-mcp` | Start the MCP stdio server. |
| MCP `search` | Bounded repository-scoped `KnowledgeSearchResponse`. |
| MCP `get_source` | Bounded authorized source read. |

The index command takes `--workspace`, required `--repository`, optional `--branch`/`--revision`, `--backend` (currently `solr`), and JSON `--backend-settings`; matching `FETCHCORE_INDEX_*` variables are accepted. MCP runtime settings use [operator configuration](configuration.md). MCP has one authorized repository binding per process today. See the [indexing guide](../guides/self-hosted-indexing.md) and [MCP guide](../guides/mcp.md).
