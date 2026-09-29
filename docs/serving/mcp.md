# FetchCore MCP serving — P0

FetchCore exposes repository retrieval to MCP clients through a transport-neutral
application boundary.

```text
MCP client
   ↓
MCPServer adapter
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

The tool returns the native FetchCore `KnowledgeSearchResponse` mapping,
including source identity, provenance and backend metadata.

### `get_source`

Inputs:

- `source_ref`
- optional `repository`
- optional `line_start` / `line_end`
- optional `context_lines`

The application service resolves the source only inside an authorized
`RepositoryBinding`. Path traversal and resolved-path escape are rejected and
source output is bounded.

## Local stdio server

The simplest configuration uses the local lexical backend:

```bash
export FETCHCORE_MCP_WORKSPACE="$PWD"
export FETCHCORE_MCP_REPOSITORY="owner/repo"
export FETCHCORE_MCP_BACKEND="local-semantic"

fetchcore-mcp
```

The process serves MCP over stdio.

For development with MCP Inspector:

```bash
mcp dev src/fetchcore/mcp_server.py
```

## Solr hybrid example

Assuming an already indexed FetchCore-compatible Solr collection:

```bash
export FETCHCORE_MCP_WORKSPACE="/path/to/repository"
export FETCHCORE_MCP_REPOSITORY="oalangomes/NeuroTrack_MS"
export FETCHCORE_MCP_BACKEND="solr"
export FETCHCORE_MCP_BACKEND_SETTINGS='{
  "url": "http://localhost:8983/solr",
  "collection": "agentsorch-knowledge",
  "projection_version": "3"
}'
export FETCHCORE_MCP_RETRIEVAL='{
  "dense": {
    "enabled": true,
    "embedding": {
      "provider": "ollama",
      "model": "embeddinggemma"
    }
  },
  "hybrid": {
    "enabled": true,
    "strategy": "rrf"
  }
}'
export OLLAMA_URL="http://172.21.32.1:11434"

fetchcore-mcp
```

## Environment contract

- `FETCHCORE_MCP_WORKSPACE`
- `FETCHCORE_MCP_REPOSITORY`
- `FETCHCORE_MCP_BACKEND`
- `FETCHCORE_MCP_REVISION` (optional)
- `FETCHCORE_MCP_BACKEND_SETTINGS` (JSON object)
- `FETCHCORE_MCP_RETRIEVAL` (JSON object, optional)
- `FETCHCORE_MCP_KNOWLEDGE_TYPES` (comma-separated, optional)
- `FETCHCORE_MCP_REPOSITORY_PATHS` (comma-separated, optional)

P0 exposes one authorized repository binding per server process. Federated
multi-repository MCP search remains a separate benchmarked capability rather
than being improvised in this serving slice.

## Deferred

- `find_symbol`
- graph-backed `get_related`
- multi-repository result fusion
- Streamable HTTP deployment and authentication
- organization-level ACL providers
