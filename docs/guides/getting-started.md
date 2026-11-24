# Getting started

FetchCore is a backend-neutral repository knowledge and retrieval engine. The quickest contributor path is Python; the container path exists to make runtime packaging reproducible.

## Requirements

- CPython 3.11–3.15
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

## Docker development, operational and benchmark environments

FetchCore has exactly three **supported** Docker Compose projects. Each has
SolrCloud, API, MCP and Search Lab on a private project network:

- `fetchcore-dev` (`compose.dev.yml`)
- `fetchcore` operational (`compose.yml`)
- `fetchcore-benchmark` (`compose.benchmark.yml`)

DEV and OPERATIONAL use their existing external SolrCloud volumes; BENCH is
disposable. Starting the services does not create a collection, index a
repository or change a snapshot. All internal retrieval uses
`http://solr:8983/solr`, not a published Docker Desktop port.

From the repository root:

```bash
docker compose -f compose.dev.yml up -d --build
# Search Lab: http://127.0.0.1:18767
# MCP: http://127.0.0.1:18766/mcp
# DEV Solr: http://127.0.0.1:18983/solr
```

**Production requires a deliberate migration** from the historical Solr and
MCP projects, an independently verified backup, and pinned
`FETCHCORE_OPERATIONAL_SOURCE_PATH` and
`FETCHCORE_OPERATIONAL_RUNTIME_CONFIG`. Do not execute `docker compose up`
against PROD as a development default.

See the [three-project operational runbook](../operations/docker-compose-three-environments.md)
for ports, volumes, commands and rollback; historical shared infrastructure
instructions describe the previous topology and are not authorized start paths.

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
