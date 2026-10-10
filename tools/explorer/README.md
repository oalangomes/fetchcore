# FetchCore Search Lab (local developer tool)

An **optional, local-only** retrieval workbench. The core remains headless through CLI, MCP and Python. No React or FastAPI dependency enters the core package.

## Features
- Live retrieval through existing `RetrievalApplicationService`, never mock data.
- Dual `SearchIntent v2` editors: bounded visual **AND/OR nested expression tree** (ordered branches, typed anchors with origin), exact/semantic terms, hints, expansions and typed signals; full JSON remains lossless.
- Operator-authorized repository and collection/index selector; selecting an index does **not** perform federated retrieval. Backend/provenance/source authorization remains application-owned.
- Read-only operator backend/capabilities; bounded request scope mode, result limit and knowledge types.
- Result inspection: source ref, revision, checksum, line ranges, content, `QueryMatchEvidence`, `EvidenceObservation`, relationships and raw metadata.
- Authorized source reads; telemetry/stages, candidate counts and fingerprints when returned.
- **Retrieval Comparison Lab** (#126): side-by-side A/B with optional C arm from real historical runs, candidate rank/presence, measured cost, provenance and explicit incompatible/unverified snapshot warnings.
- Explicit structured errors with no silent fallback.
- On-demand **operational readiness** view for selected authorized targets (#134): separates backend reachability, schema, snapshot, currentness, fingerprint and embedding readiness from retrieval quality.


## Retrieval Comparison Lab (#126)

Run at least two searches in Search Lab or import captured `RunManifest v1`
bundles through the History panel. **Comparison never executes retrieval** and
does not choose raw backend connections. Choose two (A/B) or three (A/B/C)
captured runs. The third arm renders a second independent A/C comparison; it
does not fuse candidates.

The lab shows retained candidate overlap, source identity, rank and rank
movement, observed lexical/dense channel attributions **only when actually
reported**, duration, stage timing, candidates examined/returned, character
counts and time-to-first-candidate when available. When telemetry is absent,
the corresponding cost is **not measured**, never inferred or reconstructed.

An identity is the repository `source_ref` plus stored revision and checksum.
Changes in checksum distinguish two pieces of evidence even if a file path
stays the same. Backend-local IDs are displayed with an explicit **weak
identity** warning; do not interpret source-ref-less IDs as cross-backend
semantic equivalence. Rank movement is displayed only for strong, shared
candidate identities in fingerprint-aligned runs.

**Comparison readiness is not process health.** The UI distinguishes:
- `snapshot-aligned`: both runs have the same authorized repository, bound
  revision, backend/collection, SearchIntent, K/scope and matching *reported*
  projection and indexing/snapshot fingerprints;
- `unverified`: the query/revision/context appear consistent, but required
  fingerprints are missing, so an equivalent index/snapshot is **not proven**;
- `incompatible`: the inputs, revision, collection/backend, K/budget,
  projection/embedding/serializer identity or observed result status differ.
  Diff rows remain inspectable but are **not** a controlled quality benchmark.

Strategy/mode variations are explicit observations, not quality verdicts. In
particular, a Solr lexical default does not automatically activate vectors;
real dense/hybrid comparison requires the operator to complete #130. Other
backends require the relevant operational parity in #123. The lab works with
existing imported manifests in the meantime. The `Exportar diff JSON v1`
action contains only normalized observed candidates, counts and measured
telemetry; it does not include source bodies or service credentials.

For provenance navigation, a recorded candidate only opens the current live
result when the active retrieval response is on the same authorized target
and its `source_ref`, revision and checksum match exactly. An imported
manifest cannot grant permission to read current workspace source. Quality,
Ground Truth, Recall@K and PASS/FAIL regression gates belong to external
**retrievalgate**, not FetchCore.

## Run History and strict Replay (#146)

**Histórico de execuções** records up to **20** live search outcomes per browser
tab (including typed failures). This is strictly **in-memory**: no cookies,
`localStorage`, browser databases, server-side run storage or background
requests. Reloading or closing the tab discards the history. Explicit import,
export and clear are available; clearing requires confirmation.

Export is a **versioned bundle v1** (max 512 KB). It preserves the exact
`SearchIntent`, search scope (`mode`, result limit and knowledge types),
authorized target identity (repository/backend/collection/revision),
observed rank/IDs, source refs where canonical, checksum, typed match summaries,
stage latencies, measured telemetry and actual reported compatible fingerprints.
Storage budgets are 20 runs, at most 100 candidates/run, 12 match summaries per
candidate and 24 KB/run. If bounded capture truncates evidence, the manifest
explicitly records `truncated=true`. Missing telemetry is **null**, not
synthesized values. No source body, machine path, provider connection URL,
raw backend response metadata or credentials are saved. A manually exported
bundle **may still contain sensitive query terms, identifiers and source refs**:
review before sharing.

Imported bundles are validated and normalized before becoming visible;
unsupported versions, duplicate IDs, invalid SearchIntent structures, oversized
payloads and noncanonical source paths fail closed. Import does **not** create
repository bindings, index or execute searches.

**Restaurar query** loads only the recorded SearchIntent and explicit search
parameters into the current editor. If the old target has been removed, it
does **not** grant a new target; the current operator-authorized target remains
unchanged. Restoring is not replaying and makes no network request.

**Replay verificado** is intentionally strict: it calls the on-demand readiness
endpoint on the currently authorized target, verifies identical backend,
repository, collection, retrieval policy, bound requested/indexed revision and
comparable observed fingerprints, then and only then submits an **entirely
new** search through the existing application service. Missing metadata,
stale revisions, changed fingerprints, failed runs and unknown/unready state
block replay with explicit reasons. No implicit semantic fallback, auto-reindex
or success/satisfaction assertion. Default unbound revisions will commonly
block verified replay; use **Restaurar query** then explicitly execute a **new,
non-comparable** run. The Comparison Lab #126 may later consume these manifests,
while Ground Truth and PASS/FAIL belong to external `retrievalgate`.

## Advanced Query Builder (#145)

The visual editor supports nested `all_of` (AND: **every** declared child
must be matched inside a candidate record) and `any_of` (OR: at least one
declared alternative), literal terms and structured anchors. Operators are
**never inferred** from the natural-language query. Branch order is preserved;
children can be moved up/down. Replacing a group with a leaf explicitly
discards that subtree, so check the JSON preview before running a query.
Deleting the only child is deliberately blocked; remove the entire group
explicitly instead. Depth and node limits mirror the backend: 16 levels,
256 total expression + root anchors + expansions. Incomplete term/anchor inputs
are drafts only; search/export refuse to submit them.

Five `v1` **versioned starter presets** cover identifier, qualified symbol,
file path, architecture and behavior. Preset application requires choosing
`Substituir intenção` or `Combinar listas`, then clicking **Aplicar preset**.
Merge retains the original canonical query and appends explicit anchors, hints,
expansions and signals (including `origin`). It **never invents OR/AND** for
two independent expression trees; that conflict fails closed. Presets have
example terms/paths that operators must tailor to their own repositories.

JSON import only replaces the draft after explicit confirmation and full
schema validation. JSON export writes the complete valid intent as a local file
(no backend credentials, workspace paths or target authorization are embedded).
Schema v1 is accepted in JSON mode but requires an explicit v2 migration
before entering the advanced visual builder. Unknown fields or versions,
invalid branches, missing required values, and excessive depth/size are
rejected before a search rather than silently rewritten.

**Inspecionar payload final** displays the actual bounded request envelope:
`target_id`, canonical repository binding, `SearchIntent`, search mode,
knowledge types and result limit. Scope/policy/budget are not silently folded
into the query's semantic content. The Python contract remains the final
authority on accepted structures. This is a developer interface, not a
semantic satisfaction or evidence-approval UI.

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

`tools/explorer/compose.yaml` remains only as a **thin compatibility alias**
for existing commands. It now uses the same `fetchcore` Compose project as the
root entrypoint, so Search Lab no longer creates a separate
`fetchcore-lab` project, Solr resource, or volume namespace.

Existing root `.env` files with `COMPOSE_FILE=tools/explorer/compose.yaml`
continue selecting the compatibility entrypoint until you **remove that line**.
The new `.env.example` contains no `COMPOSE_FILE`. For Solr use
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
