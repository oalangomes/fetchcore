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

## OPERATIONAL loopback Streamable HTTP

The repository includes a single-binding OPERATIONAL MCP and Search Lab
overlay for the existing OPERATIONAL Solr collection. The Lab API and MCP
service load the same runtime file, so they share the repository, pinned
revision, collection and hybrid retrieval configuration (BM25 + dense with
RRF). Both use the configured Ollama embedding model. The Lab web UI
reaches its API through the existing application boundary; MCP clients use the
separate MCP endpoint. The overlay attaches to the existing
`fetchcore-operational_default` network and does not start or recreate a
backend. Workspace and runtime configuration mounts are read-only.

The default Ollama endpoint is `http://host.docker.internal:11434`; override it
with `FETCHCORE_OPERATIONAL_OLLAMA_URL` when the embedding service uses another
host-reachable URL. Ollama must already be running with `embeddinggemma` and
768-dimensional vectors available. The overlay does not start Ollama or pull a
model.

From the repository root, build and start the MCP service and Lab:

```bash
docker compose -f infra/compose/operational-mcp.yml up -d --build mcp api lab
```

The Lab is available at `http://127.0.0.1:38767`; its API and MCP process use
the same configured application runtime. The MCP endpoint is
`http://127.0.0.1:38766/mcp`. It exposes only `search` and `get_source` through
the native `fetchcore.serving.mcp` adapter.
Use `docker compose -f infra/compose/operational-mcp.yml logs mcp` to inspect
MCP startup failures and `docker compose -f infra/compose/operational-mcp.yml logs api lab`
for Lab startup failures. Stop the stack with
`docker compose -f infra/compose/operational-mcp.yml stop mcp api lab`. Before
starting, verify that the workspace HEAD matches `runtime.revision` and that
the configured collection snapshot is ready at the same revision.

### Reconcile after an OPERATIONAL index update

After the index workflow succeeds, run explicit operator reconciliation on the
host that owns the materialized workspace and Compose deployment. It reads the
active Solr snapshot and verifies the workspace HEAD, schema, projection,
indexing and embedding identities, and worktree currentness. A failed check
leaves the serving binding unchanged. Host URLs below are used for validation;
the generated serving config retains the container URLs.

```bash
export FETCHCORE_OPERATIONAL_RUNTIME_CONFIG="$HOME/.config/fetchcore/operational-mcp.runtime.yml"
mkdir -p "$(dirname "$FETCHCORE_OPERATIONAL_RUNTIME_CONFIG")"
PYTHONPATH=src python -m fetchcore.operational.reconcile \
  --config infra/compose/operational-mcp.runtime.yml \
  --workspace "$PWD" \
  --solr-url http://127.0.0.1:38983/solr \
  --ollama-url http://127.0.0.1:11434 \
  --output "$FETCHCORE_OPERATIONAL_RUNTIME_CONFIG" \
  --activate
```

Omit `--activate` for a read-only compatibility check. Keep
`FETCHCORE_OPERATIONAL_RUNTIME_CONFIG` in the operator's Compose environment
for later `docker compose up` commands. With `--activate`, the command first
reads `docker compose config --format json` with the activation environment.
Both `mcp` and `api` must bind the validated host workspace at the runtime's
container workspace path and the generated runtime file at
`/etc/fetchcore/operational-mcp.yml`, read-only. A different checkout, missing
or overlapping mount, or runtime environment override is rejected as
`serving_binding_mismatch` before any runtime file is written or container
is recreated. Use `--compose-file` to select the deployment that mounts the
authorized workspace when working with multiple checkouts.
Overlap checks include `configs` and `secrets`, their default destinations,
and normalized container paths. Mounts outside the workspace and runtime
file remain permitted; malformed mount declarations are rejected.

The command replaces only the
operator-owned runtime file and force-recreates `mcp` and `api` with
`--no-deps`, refreshing Docker Desktop/WSL file bind mounts. It does not
recreate Solr, snapshots or the Lab web container. The Lab proxy uses Docker
DNS with a one-second cache so it can follow the replacement API's address
without a restart. Existing Lab images must be rebuilt once to adopt this
proxy configuration. Schema, snapshot and connectivity failures retain their
observed error categories; an unknown indexed revision is not evidence of a
revision mismatch. A dirty worktree or unknown
change coverage blocks activation. No fetch, checkout or reindex is performed.

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
