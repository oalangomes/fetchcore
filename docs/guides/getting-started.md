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

## Shared developer infrastructure and Search Lab

Start or reuse the DEV backend explicitly, then start the Search Lab on its
existing network. No resource is prepared or indexed by either command.

```bash
python scripts/shared_infra.py up --environment dev --repository oalangomes/fetchcore --backend solr
python scripts/shared_infra.py app --environment dev --repository oalangomes/fetchcore --backend solr
```

Open `http://127.0.0.1:5173`. The API is proxied at `/api`, and MCP Streamable
HTTP is at `http://127.0.0.1:8766/mcp`. The selected Solr backend is published at
`http://127.0.0.1:18983/solr`; app containers use `solr:8983/solr` on the existing
`fetchcore-shared_default` network. DEV and BENCH share the service but use
different logical resources. Search Lab shows the authorized environment,
backend, repository, resource and on-demand retrieval readiness. API health alone
does not mean that an index snapshot exists.

Inspect routing and readiness before writing:

```bash
python scripts/shared_infra.py env --environment dev --repository oalangomes/fetchcore
python scripts/shared_infra.py ready --environment dev --repository oalangomes/fetchcore --backend solr
python scripts/shared_infra.py run --environment dev --repository oalangomes/fetchcore -- fetchcore readiness --json
```

Prepare a Solr resource with `python -m fetchcore.operational.solr_runtime prepare`
using the emitted URL, resource and repository. Explicit first creation uses
`--provision-mode provision_if_missing --create-if-missing`; later checks use
`--provision-mode reuse_only`.
Indexing then requires an explicit `fetchcore index bootstrap`, registration,
plan and sync. See [shared infrastructure](shared-development-infrastructure.md)
and [repository indexing](repository-indexing.md) for the complete commands and
fingerprint checks. The backend-neutral CLI, API, MCP and SDK use the same
`FETCHCORE_*` binding when supplied explicitly.

The same Lab exposes an authorized BENCH target on a separate logical resource.
Use `--bench-repository` and `--bench-source-path` when BENCH uses a different
repository. A performance claim requires `--dedicated --performance
--exclusive-host` and the dedicated BENCH project. OPERATIONAL uses its own
persistent project, volumes and `reuse_only` policy. Stop an app project without
removing its backend data:

```bash
docker compose -p fetchcore-lab -f infra/compose/app.yml -f infra/compose/shared-app.yml stop
```

The root `compose.yml` and `tools/explorer/compose.yaml` remain legacy
compatibility entry points for existing CI and deployments. They create a
separate `fetchcore` Solr instance on port 8983; use them only when that
isolated legacy stack is intended. `docker compose down` without `-v` preserves
its named volumes. The shared infrastructure tool never deletes DEV or
OPERATIONAL volumes.

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
