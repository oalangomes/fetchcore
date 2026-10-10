# FetchCore Search Lab (local developer tool)

An **optional, local-only** retrieval workbench. The core remains headless through CLI, MCP and Python. No React or FastAPI dependency enters the core package.

## Features
- Live retrieval through existing `RetrievalApplicationService`, never mock data.
- Dual `SearchIntent v2` editors: visual form for exact/semantic terms, hints, required anchors, expansions and typed signals; lossless full JSON editor for expressions and all advanced fields.
- Operator-authorized repository and collection/index selector; selecting an index does **not** perform federated retrieval. Backend/provenance/source authorization remains application-owned.
- Read-only operator backend/capabilities; bounded request scope mode, result limit and knowledge types.
- Result inspection: source ref, revision, checksum, line ranges, content, `QueryMatchEvidence`, `EvidenceObservation`, relationships and raw metadata.
- Authorized source reads; telemetry/stages, candidate counts and fingerprints when returned.
- Explicit structured errors with no silent fallback.


## Query builder and operator-owned targets

The Search Lab has **Formulário** and **JSON completo** modes. Both operate on
the **same SearchIntent v2 JSON**; changing a visible field in the form preserves
unrepresented fields (notably boolean expression branches, signal metadata,
expansion provenance and schema_version). Invalid/incomplete JSON is preserved,
reported and never submitted; it cannot be silently converted to a broad query.

The API exposes an allowlisted set of retrieval targets in `/api/config`.
A target is **one repository binding + one backend projection/index + one
retrieval policy**, not an unrestricted collection browser. The browser sends
only `target_id` and the existing SearchIntent to the application service.
Raw Solr URL, filesystem workspace, backend credentials and runtime settings
cannot be supplied by browser requests. A missing/unauthorized target fails
explicitly. Source reads use the same target binding.

The default target is the existing configured `FETCHCORE_BACKEND`,
`FETCHCORE_REPOSITORY` and collection/index settings. To expose **additional**
operator-authorized targets, inject `FETCHCORE_EXPLORER_TARGETS` as a JSON array
via the API process environment (or your root `.env` file):

```dotenv
FETCHCORE_EXPLORER_TARGETS=[{"id":"second-index","repository":"oalangomes/fetchcore","workspace":"/workspace","backend":"solr","backend_settings":{"url":"http://solr:8983/solr","collection":"fetchcore-other"}}]
```

The entry's `id` must be unique, and `workspace` must be an **absolute
existing container path**. `repository` is the exact authorized canonical
repository identity. Optional `revision` and `retrieval` settings may be set
per target. Schema/backend/embedding compatibility is validated through the
existing FetchCore application/adapter contracts. The second collection must
already exist and be indexed: listing a target does **not** create/provision it.

To expose a **second repository**, mount its source read-only into the
`api` container **explicitly** using an operator-owned Compose override:

```yaml
# compose.override.yml, repository root, local-only example
services:
  api:
    volumes:
      - /absolute/host/path/to/other-repo:/repos/other:ro
```

Then add an allowlist entry with `workspace: "/repos/other"`,
`repository: "owner/other"` and the corresponding existing indexed backend
collection. Do not mount your entire filesystem root or use client-supplied
paths. API targets are **not** automatically exposed to the default MCP
process or the on-demand indexer; those retain their own explicit binding.
To configure those consumers, use their existing operator-owned runtime
settings.

**Limit:** each choice executes a **single-target** search. This is neither
multi-repository federation (#42) nor dynamic Solr collection enumeration.
The UI cannot change indexing policy or activate dense/hybrid on an
unprepared index (#123).

## Docker Compose: repository-root default

The root `compose.yml` is the canonical developer entrypoint. It reuses
`infra/compose/app.yml` and `infra/compose/solr.yml` without duplicating
service definitions. Solr lexical/BM25 is selected for **this optional Explorer
only**; FetchCore's headless SDK/CLI/stdio MCP defaults are unchanged.

Run from the **repository root**:

```bash
docker compose config --quiet
docker compose up -d --build --wait
docker compose ps
```

| Service | Internal endpoint | Loopback-only host endpoint |
| --- | --- | --- |
| Frontend (Nginx/React) | `web:8080` | `http://127.0.0.1:5173` |
| API (FastAPI) | `api:8765` | proxied at `http://127.0.0.1:5173/api/` |
| MCP Streamable HTTP | `mcp:8766/mcp` | `http://127.0.0.1:8766/mcp` |
| Solr 9.10.1 | `solr:8983/solr` | `http://127.0.0.1:8983/solr` |

These services start together, but **an empty Solr collection is not indexed**.
After choosing your authorized repository, run an explicit initial index/sync:

```bash
docker compose --profile indexing run --rm indexer fetchcore index bootstrap
docker compose --profile indexing run --rm indexer fetchcore index status
docker compose --profile indexing run --rm indexer fetchcore index sync
docker compose exec -T api python -m tools.explorer.docker.smoke
```

The last step is a fixture-specific smoke, meant for the CI demo corpus.
The legacy `index sync` has an explicit operator-invoked initial/recovery
policy; the registered `index sync <repo>` never falls back to rebuild. Neither
runs during `up`, frontend refresh or search. No automatic embedding downloads.

To index a different repository, set the two values before creating containers
(or copy `.env.example` to repository-root `.env` and edit them):

```bash
FETCHCORE_SOURCE_PATH=/absolute/path/to/repo \
FETCHCORE_REPOSITORY=owner/other-repo docker compose up -d --build --wait
```

Mount only the permitted repository, **read-only**; use an absolute custom path.
The default `FETCHCORE_SOURCE_PATH=../..` means repository root **relative
to `infra/compose/app.yml`**, not your shell working directory. API, MCP
and indexer use the same mounted source and repository identity.

### Standalone backend Compose files

```bash
docker compose -p fetchcore-solr -f infra/compose/solr.yml up -d --wait
docker compose -p fetchcore-opensearch -f infra/compose/opensearch.yml up -d --wait
docker compose -p fetchcore-qdrant -f infra/compose/qdrant.yml up -d --wait
```

These are independently runnable, without starting the frontend/MCP.
Run one at a time unless you change host ports. For an alternate **full**
developer stack (with a previously indexed, compatible backend):

```bash
FETCHCORE_BACKEND=opensearch docker compose -p fetchcore-opensearch \
  -f infra/compose/app.yml -f infra/compose/opensearch.yml up -d --build --wait
```

For Qdrant use `FETCHCORE_BACKEND=qdrant-vector`, the `qdrant.yml` file,
and a compatible configured embedding provider/model. Non-Solr operational
indexing is **not implemented yet** (#123). The `indexer` profile in
`app.yml` remains explicitly Solr-only. A backend container being healthy
does not imply its index is present or compatible.

### Legacy Compose and runtime compatibility

`tools/explorer/compose.yaml` remains a **thin compatibility wrapper**,
so existing commands with `docker compose -f tools/explorer/compose.yaml`
keep the original project name `fetchcore-lab`. Prefer the root command.
Root `compose.yml` uses project `fetchcore`: its Solr volume is **separate**
from `fetchcore-lab_solr-data`; both persist unless explicitly deleted.
You must explicitly index or migrate data when switching project names.

Existing root `.env` files with `COMPOSE_FILE=tools/explorer/compose.yaml`
continue selecting the old entrypoint until you **remove that line**. The
new `.env.example` contains no `COMPOSE_FILE`. For Solr use
`FETCHCORE_SOLR_COLLECTION` / `FETCHCORE_SOLR_PORT`; historical root
`SOLR_COLLECTION` / `SOLR_PORT` no longer configure the Explorer stack.

The original opt-in `docker compose --profile runtime run --rm fetchcore`
still starts the headless stdio MCP image, using the existing legacy
`FETCHCORE_MCP_*` environment contract.

Operator-owned `FETCHCORE_RETRIEVAL` can be placed in root `.env`
for an explicit retrieval policy. Do not enable dense/hybrid until
the indexed vector snapshot and provider match; end-to-end operational
embedding readiness is tracked in #123. Ollama is **not** started or pulled
by this stack. No backend is silently substituted.

```bash
docker compose logs -f --tail=100
docker compose down   # named volumes preserved
```

### MCP client setup

Point a Streamable HTTP MCP client to `http://127.0.0.1:8766/mcp`.
The classic `fetchcore-mcp` stdio command remains available independently.
Both call the same `RetrievalApplicationService`, never the backend directly.
All exposed ports are loopback-only; this is **unauthenticated, developer-only**
deployment, not a public API.

## Run without Docker (Solr is the default backend)

Requires Python 3.11+, Node.js 20+ and Docker for the optional managed Solr example.
**Search Lab starts with Solr/BM25 lexical retrieval by default**, instead of `local-semantic`.
The FetchCore SDK, CLI, MCP and normal `load_runtime_config()` defaults are unchanged.
An explicit operator-selected backend in `FETCHCORE_BACKEND` or `FETCHCORE_CONFIG` takes precedence.
Unavailable, empty or incompatible Solr collections fail explicitly: there is **no silent local fallback**.

### First-time Solr setup

From the repository root:

```bash
python -m pip install -e .
python -m pip install -r tools/explorer/api/requirements.txt

source infra/images.lock
scripts/ci/docker-pull.sh "$FETCHCORE_SOLR_IMAGE"
docker run -d --name fetchcore-search-lab-solr \
  -p 127.0.0.1:8983:8983 \
  -v "$PWD/infra/solr/configset:/fetchcore-configset:ro" \
  "$FETCHCORE_SOLR_IMAGE" solr-precreate fetchcore-lab /fetchcore-configset
```

After Solr has started, bootstrap and index this repository (or point at a separately provisioned and indexed collection):

```bash
export FETCHCORE_INDEX_WORKSPACE="$PWD"
export FETCHCORE_INDEX_REPOSITORY="oalangomes/fetchcore"
export FETCHCORE_INDEX_BACKEND="solr"
export FETCHCORE_INDEX_BACKEND_SETTINGS='{"url":"http://127.0.0.1:8983/solr","collection":"fetchcore-lab","projection_version":"3"}'

fetchcore index bootstrap
fetchcore index sync
```

### Start the Search Lab API

Terminal 1 (from the repository root):

```bash
export FETCHCORE_WORKSPACE="$PWD"
export FETCHCORE_REPOSITORY="oalangomes/fetchcore"
export FETCHCORE_SOLR_URL="http://127.0.0.1:8983/solr"
export FETCHCORE_SOLR_COLLECTION="fetchcore-lab"
# FETCHCORE_BACKEND is intentionally unset: Search Lab selects Solr by default.

python -m uvicorn tools.explorer.api.app:app --host 127.0.0.1 --port 8765
```

Terminal 2:

```bash
cd tools/explorer/web
npm install
npm run dev
```

Open **http://127.0.0.1:5173**. Vite proxies `/api` to the loopback-only API.
Starting the UI does not provision a collection, start Solr or trigger indexing. The Search Lab never reindexes on a query.

To point the Explorer at an existing Solr, OpenSearch or Qdrant installation,
use operator-owned `FETCHCORE_CONFIG` or an explicit `FETCHCORE_BACKEND`.
Retrieval mode (lexical/dense/hybrid) remains controlled by the operator's retrieval configuration;
the `discovery`/`targeted`/`repository_source` request mode is a separate concept.
Solr lexical/BM25 works without an embedding provider; enable dense/hybrid only after the
existing embedding, schema and projection requirements are satisfied.

**Security:** bind both processes to loopback. There is no HTTP authentication or wildcard CORS.
One authorized repository binding is used; the UI cannot select arbitrary repository workspaces or endpoints.

## Test

From repository root:

```bash
python -m pip install -e ".[dev]"
python -m pip install -r tools/explorer/api/requirements.txt
python -m pytest -q tools/explorer/api/test_app.py
```

From `tools/explorer/web`:

```bash
npm install
npm run build
npm test
npx playwright install --with-deps chromium
npm run test:e2e
```

Playwright starts a **real** FastAPI server and queries an indexed **Solr** collection over `tools/explorer/fixtures/demo-repo`. The GitHub Actions workflow provisions Solr, indexes the fixture and verifies real search, evidence, provenance, source reads and telemetry. For local E2E, prepare the same `fetchcore-lab` collection and index the fixture with repository identity `demo/catalog` before running Playwright. Vitest checks UI and client contract preservation; pytest checks the HTTP boundary and forbidden operations.

## Boundary

Browser → Vite proxy → local FastAPI → `RetrievalApplicationService` → backend-neutral search.

No agent workflow, graph requirement, consumer ranking, LLM inference or new product HTTP serving contract.

## Limitations

- One repository binding per process; no federated multi-repo behavior.
- If an indexed result has a revision but the serving workspace is unbound or has another revision, source reads show a warning and display authorized current-workspace bytes. The original indexed provenance remains visible; equivalence is **not** claimed. An exact revision precondition is supplied only when it matches the authorized binding.
- Solr is the default Explorer backend; explicitly configured other backends are respected. Switching requires operator reconfiguration/restart.
- Graph Explorer belongs to #31, not this MVP.
- Quality/ground-truth evaluation is the responsibility of retrievalgate, not this interface.
