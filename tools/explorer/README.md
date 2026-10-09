# FetchCore Search Lab (local developer tool)

An **optional, local-only** retrieval workbench. The core remains headless through CLI, MCP and Python. No React or FastAPI dependency enters the core package.

## Features
- Live retrieval through existing `RetrievalApplicationService`, never mock data.
- Editable `SearchIntent v2` JSON (anchors, expression, signals, expansions and hints).
- Read-only operator backend/capabilities; bounded request scope mode, result limit and knowledge types.
- Result inspection: source ref, revision, checksum, line ranges, content, `QueryMatchEvidence`, `EvidenceObservation`, relationships and raw metadata.
- Authorized source reads; telemetry/stages, candidate counts and fingerprints when returned.
- Explicit structured errors with no silent fallback.

## Run (Solr is the default backend)

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

docker run -d --name fetchcore-search-lab-solr \
  -p 127.0.0.1:8983:8983 \
  -v "$PWD/infra/solr/configset:/fetchcore-configset:ro" \
  solr:9.10.1 solr-precreate fetchcore-lab /fetchcore-configset
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
