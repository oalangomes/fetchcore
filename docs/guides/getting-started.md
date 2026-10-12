# Getting started

FetchCore is a backend-neutral repository knowledge and retrieval engine. The quickest contributor path is Python; the container path exists to make runtime packaging reproducible.

## Requirements

- Python 3.11+
- Git
- Docker with the Compose plugin only when using the container/runtime or optional backend services

## Python development setup

From the repository root:

```bash
python -m pip install -e ".[dev]"
```

The repository provides small convenience targets, while keeping the underlying commands visible:

```bash
make help
make check
```

`make check` runs the deterministic Python quality path:

```text
ruff
  ↓
mypy
  ↓
pytest + coverage collection
  ↓
python package build
```

## Run the MCP server locally

MCP serving is optional and uses the existing application boundary:

```text
MCP -> RetrievalApplicationService -> KnowledgeSearchBackend
```

The local-semantic backend is the default serving backend:

```bash
export FETCHCORE_WORKSPACE="$PWD"
export FETCHCORE_REPOSITORY="owner/repository"
export FETCHCORE_BACKEND="local-semantic"

fetchcore-mcp
```

The process serves MCP over stdio. The historical `FETCHCORE_MCP_*` aliases remain accepted, but the canonical operator runtime names above take precedence. Starting the server alone does not index a repository; retrieval requires a compatible, authorized source/index. See [MCP serving](mcp.md) for the complete environment contract and backend examples.

## Build the FetchCore container

The container contains FetchCore only. Solr, OpenSearch, Qdrant and Ollama are not embedded in the image.

```bash
make docker-build
make docker-smoke
```

Equivalent direct commands:

```bash
docker build -t fetchcore:dev .
docker run --rm --entrypoint sh fetchcore:dev -c \
  'test "$(id -u)" -ne 0 && command -v fetchcore-mcp >/dev/null && python -c "import fetchcore"'
```

To run the MCP stdio process against the current repository:

```bash
docker run --rm -i \
  -v "$PWD:/workspace:ro" \
  -e FETCHCORE_MCP_WORKSPACE=/workspace \
  -e FETCHCORE_MCP_REPOSITORY="owner/repository" \
  fetchcore:dev
```

The image runs as a non-root user and treats the mounted repository as read-only.

## Docker Compose: default Explorer stack

The **default** root `compose.yml` is now the local, backend-neutral Search Lab
application with the **Solr lexical/BM25** adapter. Backend containers are
rebuildable projections and do not change repository knowledge authority.

From the repository root:

```bash
docker compose config --quiet
docker compose up -d --build --wait
docker compose ps
```

The default starts the **web** (http://127.0.0.1:5173), **API** (proxied at
`/api`), **MCP Streamable HTTP** (http://127.0.0.1:8766/mcp) and **Solr**
(http://127.0.0.1:8983/solr). Host ports are loopback-only, suitable for local
development, **not** authenticated network serving.

**Service health does not mean the repository is indexed.** Indexing is always
an explicit operator operation:

```bash
docker compose --profile indexing run --rm indexer fetchcore index bootstrap
docker compose --profile indexing run --rm indexer fetchcore index status
docker compose --profile indexing run --rm indexer fetchcore index sync
docker compose exec -T api python -m tools.explorer.docker.smoke
```

The legacy `index sync` may initialize a missing Solr snapshot when explicitly
invoked; registered `index sync <repo>` has its own no-implicit-rebuild policy.
No model is pulled and no collection is indexed automatically when Compose starts.
To index a different local repository, set `FETCHCORE_SOURCE_PATH` to an
**absolute host path** and `FETCHCORE_REPOSITORY` to its canonical identity
before starting. See [Explorer setup](../../tools/explorer/README.md).

### Optional standalone backends

Each backend has **one** reusable Compose definition; running it alone
does **not** start the frontend or index anything:

```bash
docker compose -p fetchcore-solr -f infra/compose/solr.yml up -d --wait
docker compose -p fetchcore-opensearch -f infra/compose/opensearch.yml up -d --wait
docker compose -p fetchcore-qdrant -f infra/compose/qdrant.yml up -d --wait
```

Run **one at a time** unless you explicitly adjust published host ports.
The pinned versions are Solr 9.10.1, OpenSearch 3.8.0 and Qdrant 1.19.0.
OpenSearch and Qdrant support retrieval according to their declared capabilities,
but full operational indexing/CLI parity remains tracked under
[issue #123](https://github.com/oalangomes/fetchcore/issues/123).

For an **alternative application stack**, combine the shared application with
the desired backend and explicitly select it:

```bash
FETCHCORE_BACKEND=opensearch docker compose -p fetchcore-opensearch \
  -f infra/compose/app.yml -f infra/compose/opensearch.yml up -d --build --wait
```

This **does not** make the current Solr-only operational `indexer` work with
OpenSearch. An alternative backend requires an independently provisioned,
compatible index. For Qdrant use `FETCHCORE_BACKEND=qdrant-vector`, the
`qdrant.yml` file and explicitly configured embeddings/vector snapshot.
No silent fallback to Solr/local-semantic occurs.

### Compatibility and teardown

The original `docker compose -f tools/explorer/compose.yaml ...` entrypoint
remains as a compatibility alias, but it now uses the same `fetchcore`
Compose project and persistent Solr volume as the root stack. Search Lab does
not create a second `fetchcore-lab` runtime namespace.

Never run `down -v` unless you intentionally want to delete the canonical
local FetchCore volume.

The old standalone `fetchcore` MCP **stdio image** remains available:

```bash
docker compose --profile runtime run --rm fetchcore
```

If an existing root `.env` contains `COMPOSE_FILE=tools/explorer/compose.yaml`,
remove that line to opt into the new root default. `make compose-config`
checks the root and optional backend compositions.

```bash
docker compose down
```

Ollama stays an externally configured optional embedding provider. Dense/hybrid
require matching vector index, model, serializer and dimensions (#123); Compose
startup alone does not make them operational.

## Contributor loop

For ordinary changes:

```bash
make check
```

For changes touching runtime packaging or local backend topology:

```bash
make runtime-check
```

Retrieval-quality changes still require the appropriate FetchCore benchmark protocol and, when used as a regression gate, external retrievalgate evaluation. Docker/Compose success is not evidence that retrieval quality improved.
