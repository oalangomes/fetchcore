# FetchCore Search Lab (local developer tool)

An **optional, local-only** retrieval workbench. The core remains headless through CLI, MCP and Python. No React or FastAPI dependency enters the core package.

## Features
- Live retrieval through existing `RetrievalApplicationService`, never mock data.
- Editable `SearchIntent v2` JSON (anchors, expression, signals, expansions and hints).
- Read-only operator backend/capabilities; bounded request scope mode, result limit and knowledge types.
- Result inspection: source ref, revision, checksum, line ranges, content, `QueryMatchEvidence`, `EvidenceObservation`, relationships and raw metadata.
- Authorized source reads; telemetry/stages, candidate counts and fingerprints when returned.
- Explicit structured errors with no silent fallback.

## Run

Requires Python 3.11+ and Node 20+. From the repository root, in terminal 1:

```bash
python -m pip install -e .
python -m pip install -r tools/explorer/api/requirements.txt

export FETCHCORE_WORKSPACE="$PWD"
export FETCHCORE_REPOSITORY="oalangomes/fetchcore"
export FETCHCORE_BACKEND="local-semantic"

python -m uvicorn tools.explorer.api.app:app --host 127.0.0.1 --port 8765
```

Terminal 2:

```bash
cd tools/explorer/web
npm install
npm run dev
```

Open **http://127.0.0.1:5173**. Vite proxies `/api` to the loopback-only API.

For Solr, OpenSearch or Qdrant, configure the authorized backend/index through operator-owned `FETCHCORE_CONFIG`, not through the browser. Retrieval mode (lexical/dense/hybrid) is owned by runtime config; request scope mode is a separate concern.

**Security:** do not expose Vite or the FastAPI bridge on untrusted networks. There is no HTTP authentication or cross-origin allowance in this MVP. The service binds one authorized repository; the UI does not accept runtime config or arbitrary repository paths.

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

Playwright starts a **real** FastAPI server and FetchCore local-semantic backend over `tools/explorer/fixtures/demo-repo`. It verifies search, evidence, provenance, source reads and telemetry end-to-end. Vitest checks UI and client contract preservation; pytest checks the HTTP boundary and forbidden operations.

## Boundary

Browser → Vite proxy → local FastAPI → `RetrievalApplicationService` → backend-neutral search.

No agent workflow, graph requirement, consumer ranking, LLM inference or new product HTTP serving contract.

## Limitations

- One repository binding per process; no federated multi-repo behavior.
- Backend configured outside UI. Switching requires operator reconfiguration/restart.
- Graph Explorer belongs to #31, not this MVP.
- Quality/ground-truth evaluation is the responsibility of retrievalgate, not this interface.
