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
export FETCHCORE_MCP_WORKSPACE="$PWD"
export FETCHCORE_MCP_REPOSITORY="owner/repository"
export FETCHCORE_MCP_BACKEND="local-semantic"

fetchcore-mcp
```

The process serves MCP over stdio. See [MCP serving](../serving/mcp.md) for the complete environment contract and backend examples.

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

## Optional backend services with Compose

Copy the example environment only when you need to customize ports/runtime values:

```bash
cp .env.example .env
```

Validate the topology without starting services:

```bash
make compose-config
```

Start one backend at a time:

```bash
docker compose --profile solr up -d solr
docker compose --profile opensearch up -d opensearch
docker compose --profile qdrant up -d qdrant
```

The pinned development versions match the current frozen baseline environment:

- Solr 9.10.1;
- OpenSearch 3.8.0;
- Qdrant 1.19.0.

Profiles do not change FetchCore retrieval semantics. They only provide reproducible optional service processes.

## Run the FetchCore image through Compose

The runtime service is also opt-in:

```bash
docker compose --profile runtime run --rm fetchcore
```

For a backend container, start that backend first and set `FETCHCORE_MCP_BACKEND` plus `FETCHCORE_MCP_BACKEND_SETTINGS` using the examples in `.env.example`.

Ollama is intentionally not started by this Compose file. Embedding remains optional and provider-specific configuration stays outside the FetchCore core contract.

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
