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

## Tools

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

### `find_symbol` and `get_related`

`find_symbol` accepts `name`, optional `repository`, `revision`, `language`,
`kind`, exact `path`, and `limit` (1..100). Names, qualified names and returned
canonical identity keys are exact lookups, not BM25/dense queries.

`get_related` accepts `source` (a returned symbol `identity` or a file
`source_ref`), optional `repository`, `revision`, `relation_types`, `direction`
(`outgoing` or `incoming`), `depth` (only `1`), `limit` (1..100), and
`require_resolution`. Symbol identities select symbol-owned observations;
file source references select imports, dependencies and semantic file relations.
Incoming navigation requires a returned symbol identity and follows only
resolved target identities from the active canonical snapshot.
Use `get_source` with the returned source reference, revision and span to hydrate
the definition safely. An unpinned binding observes the current Git revision
for this source precondition; for non-Git trees it verifies the canonical tree
revision when requested.

Both tools delegate to the same application methods as the SDK and CLI.
They extract a bounded canonical view of the authorized workspace in memory,
respecting repository indexing policy, without contacting search/vector/graph
backends or embedding providers. They preserve homonyms, overloads, producer
capabilities and provenance. Results include explicit extraction coverage,
scan diagnostics and a `limit_reached` indicator; an empty partial/unsupported
result is not proof that the repository has no matching symbol or relation.
Unavailable capabilities never cause an implicit textual/dense fallback.
See the [structural navigation reference](../reference/cli-mcp.md#exact-structural-navigation)
for response statuses, filters and bounds.

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

## Docker Streamable HTTP: DEV, PROD, BENCHMARK

Exactly three Compose projects are supported, each containing SolrCloud,
API, the native FetchCore MCP v2 server and Search Lab:

| Environment | Compose file | MCP loopback |
| --- | --- | --- |
| DEV (`fetchcore-dev`) | `compose.dev.yml` | `http://127.0.0.1:18766/mcp` |
| PROD (`fetchcore`) | `compose.yml` | `http://127.0.0.1:38766/mcp` |
| BENCH (`fetchcore-benchmark`) | `compose.benchmark.yml` | `http://127.0.0.1:48766/mcp` |

The MCP adapter delegates to `RetrievalApplicationService` and provides
`search`, `get_source`, `find_symbol`, `get_related` and opt-in
`navigate_graph`. API and MCP communicate with their own SolrCloud service
through Docker DNS `http://solr:8983/solr`.

The productive runtime YAML and pinned source are operator-owned bind mounts
required by `compose.yml`. These are not inferred from the moving developer
checkout. `FETCHCORE_OPERATIONAL_RUNTIME_CONFIG` and
`FETCHCORE_OPERATIONAL_SOURCE_PATH` must be explicitly set to verified
absolute paths; aliases, revision, schema/fingerprints and embedding provider
must match the active index. No implicit reindexing or runtime fallback occurs.

The earlier `infra/compose/operational-mcp.yml` is a compatibility alias of
the **same** `fetchcore` Compose project, not a separate stack.
The runtime reconciliation command now defaults to the root productive
`compose.yml`. It still changes only API/MCP serving mounts after checking
the selected source and snapshot; it does not rebuild Solr.

See [three-environment Compose and migration](../operations/docker-compose-three-environments.md)
before starting or recreating the productive stack. Do not run
`docker compose down -v` in DEV or PROD.

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

- graph-backed `get_related`
- multi-repository result fusion
- standalone authenticated Streamable HTTP deployment (distinct from the local Explorer's loopback HTTP transport)
- organization-level ACL providers
