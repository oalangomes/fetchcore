# MCP serving

FetchCore exposes repository retrieval to MCP clients through a transport-neutral
application boundary.

```text
MCP client
   ↓
MCPServer adapter
   ↓
application runtime bootstrap (operator config)
   ↓
RetrievalApplicationService
   ↓
RepositoryBinding / authorization scope
   ↓
KnowledgeSearchBackend
   ↓
Solr | OpenSearch | Qdrant | local-semantic
```

The MCP adapter does not call backend APIs directly and does not own AgentsOrch
lifecycle concepts.

## Install

MCP support is optional:

```bash
python -m pip install -e ".[mcp]"
```

FetchCore targets the current MCP Python SDK v2 line.

## P0 tools

### `search`

Inputs:

- `query`
- optional `repository`
- `limit` (1..100)
- optional `knowledge_types`
- optional typed `intent` (`SearchIntent` mapping validated by the application boundary)

The tool returns the native FetchCore `KnowledgeSearchResponse` mapping,
including source identity, provenance and backend metadata.

### `get_source`

Inputs:

- `source_ref`
- optional `repository`
- optional `revision` (expected materialized revision; mismatch fails without checkout or refresh)
- optional `line_start` / `line_end`
- optional `context_lines`

The application service resolves the source only inside an authorized
`RepositoryBinding`. Path traversal and resolved-path escape are rejected and
source output is bounded. When supplied, `revision` is a precondition on the
authorized materialization, not an instruction to fetch or check out another
revision; the returned `SourceReadResult.revision` remains the observed revision.

## Local stdio server

The simplest configuration uses the local lexical backend:

```bash
export FETCHCORE_WORKSPACE="$PWD"
export FETCHCORE_REPOSITORY="owner/repo"
export FETCHCORE_BACKEND="local-semantic"

fetchcore-mcp
```

The process serves MCP over stdio.

For development with MCP Inspector:

```bash
mcp dev src/fetchcore/mcp_server.py
```

## Solr hybrid example

Assuming an already indexed FetchCore-compatible Solr collection:

Create an operator-owned YAML file using the
[runtime configuration guide](runtime-configuration.md), set
`FETCHCORE_CONFIG` to its path, and run `fetchcore-mcp`. The MCP adapter
delegates loading and composition to the application runtime boundary.

## Historical environment contract

- `FETCHCORE_MCP_WORKSPACE`
- `FETCHCORE_MCP_REPOSITORY`
- `FETCHCORE_MCP_BACKEND`
- `FETCHCORE_MCP_REVISION` (optional)
- `FETCHCORE_MCP_BACKEND_SETTINGS` (JSON object)
- `FETCHCORE_MCP_RETRIEVAL` (JSON object, optional)
- `FETCHCORE_MCP_KNOWLEDGE_TYPES` (comma-separated, optional)
- `FETCHCORE_MCP_REPOSITORY_PATHS` (comma-separated, optional)

These names remain compatibility inputs. New deployments should use the
runtime-level names and versioned YAML described in the guide. The historical
JSON-in-ENV fields are retained for existing integrations, including dogfood.

P0 exposes one authorized repository binding per server process. Federated
multi-repository MCP search remains a separate benchmarked capability rather
than being improvised in this serving slice.

## Deferred

- `find_symbol`
- graph-backed `get_related`
- multi-repository result fusion
- standalone authenticated Streamable HTTP deployment (distinct from the local Explorer's loopback HTTP transport)
- organization-level ACL providers
