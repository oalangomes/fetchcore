# Shared infrastructure P0 — local execution, 2026-10-16

This is a local functional validation, not a performance equivalence claim.
The machine was running unrelated workloads and an existing benchmark process;
no exclusive-host attestation was made. No remote production deployment changed.

## Latest recheck — 2026-10-16 21:23 UTC

**Current acceptance: BLOCKED.** The successful live runs below are historical
observations from earlier in this session. The current Docker Desktop daemon
returns `Docker Desktop is unable to start`; all six shared/operational backend
HTTP endpoints refuse connections. Active containers and volume persistence
cannot currently be inspected. No reset, deletion or recreation was attempted.

| Backend | DEV | BENCH | OPERATIONAL | Readiness | Persistence | Retrieval |
| --- | --- | --- | --- | --- | --- | --- |
| Solr | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED |
| OpenSearch | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED |
| Qdrant | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED |

Evidence for every blocked cell: daemon failure and refused endpoints in
`/home/operator/.local/state/fetchcore/shared-infra-p0-20261016/recheck-212339Z/runtime.json`.
Persistence is unverified in this recheck, not evidence of data loss. Restoring
the Docker Desktop daemon and repeating live checks is required before closure.

Ollama: **PASS** for WSL-host connectivity at
`http://172.21.32.1:11434/api/tags` (HTTP 200); **BLOCKED** for a fresh container
connectivity check while Docker is unavailable. This gateway is host-specific;
use an explicit `--ollama-url` override for WSL commands. Earlier container
embedding and retrieval evidence remains recorded below.

Additional infrastructure corrections from this review:

- Backend selection now routes indexing to the same selected backend. It no longer
  silently exports a Solr writer when OpenSearch or Qdrant was selected.
  Unsupported operational indexing fails explicitly with the existing CLI
  `unsupported_backend` error, representing the unsupported capability tracked by
  issue #123. This change does not implement that lifecycle.
- Qdrant readiness uses `/readyz`, so liveness cannot stand in for readiness.
- Qdrant preparation rejects incompatible distance/sparse modifier and malformed
  collection inventories before creation. Existing resources are not rewritten.

Validation after these fixes: **48 focused tests passed**;
`.venv/bin/python -m pytest -q`: **861 passed, 7 skipped**. The seven opt-in live
integration tests were not enabled and are not fresh live passes. Ruff passed;
`MYPYPATH=src .venv/bin/mypy scripts/shared_infra.py` passed. Regression tests cover
all nine environment/backend indexing routes, real CLI rejection before Solr
writer construction, Qdrant 503 readiness and incompatible resource schemas.
No retrieval algorithm or core contract changed.

## Git and scope

Work branch: `codex/shared-infra-p0`, starting HEAD
`4b21c08ac0cff66a5a57c657213f110e847b7b11`, base `ff962ab`.
The existing P0 commit added routing, owned Compose projects, isolated names,
cleanup guards, workflow defaults and benchmark endpoint routing.
The pre-existing untracked `.vscode/` directory was preserved.
No branch was created, no merge/push was performed, and no existing change was rewritten.

Corrections in this validation:

- `scripts/shared_infra.py`: explicit operator-owned shared endpoint JSON, coherent
  indexing/serving Solr URL, isolated-port guard and `up --no-recreate`.
- `infra/compose/shared.yml` and `infra/solr/start-shared.sh`: install the unchanged
  pinned configset in standalone Solr's actual configset base; refuse overwrite.
- Endpoint example, operational regression coverage and this runbook/report.

No core contract, retrieval algorithm, projection/serializer identity, schema or
frozen benchmark artifact was changed.

## Local operator files and corpus

Evidence and reusable commands are at:

```text
/home/operator/.local/state/fetchcore/shared-infra-p0-20261016/
```

The directory contains before/after inventories, command output, routing JSON,
registries, `fc`, `serve.sh`, `serving.compose.yml`, backend smoke code and results.
It is outside the repository so evidence cannot recursively enter the corpus.
The `corpus/` subdirectory is a clean detached worktree at the starting HEAD above,
with the existing origin/repository identity. All three Solr indexes use this same
revision. The operator registries are separate (`dev-registry`, `bench-registry`,
`operational-registry`). Daily commands below address this pinned corpus; they do
not follow later branch edits automatically.

## Infrastructure and resources

| Consumer | Service | Effective resource |
| --- | --- | --- |
| DEV | Existing Solr `127.0.0.1:8983/solr` | `fc-dev-fetchcore-635ab7352c2850f2` |
| Functional BENCH | Same existing Solr | `fc-bench-fetchcore-635ab7352c2850f2` |
| OPERATIONAL | Isolated Solr `127.0.0.1:38983/solr` | `fc-operational-fetchcore-635ab7352c2850f2` |
| Dedicated BENCH | Isolated Solr `127.0.0.1:28983/solr` | `fc-bench-fetchcore-635ab7352c2850f2` (empty, prepared) |
| Integration tests | Existing Solr | `fc-bench-integration-p0-20261016` (test records cleaned) |
| DEV/BENCH OpenSearch | Existing `127.0.0.1:9200` | Separate prefixes `fc-dev-fetchcore-635ab7352c2850f2` / `fc-bench-fetchcore-635ab7352c2850f2` |
| DEV/BENCH Qdrant | Existing `127.0.0.1:6333` | Same distinct environment prefixes |

OpenSearch and Qdrant adapters append repository hashes to aliases and immutable
concrete generation suffixes. The exact inventories are in
`opensearch-aliases.json` / `qdrant-aliases.json`; do not treat a prefix as a concrete
index/collection. Their smoke corpus uses the existing `README.md` include pattern
(142 canonical documents including matching repository documentation), not the full
Solr corpus. This is backend/provenance validation, not a cross-backend quality comparison.

Existing services retained: `fetchcore-solr-1`, `fetchcore-opensearch-1`,
`fetchcore-qdrant-1`, existing Search Lab and all unrelated containers.
Existing persistent volumes retained: `fetchcore_solr-data`,
`fetchcore_opensearch-data`, `fetchcore_qdrant-data`, plus every other inventoried volume.
New backend containers: `fetchcore-operational-solr-1` and
`fetchcore-bench-dedicated-solr-1`, with corresponding
`fetchcore-operational_solr-data` / `fetchcore-bench-dedicated_solr-data` volumes and
`fetchcore-operational_default` / `fetchcore-bench-dedicated_default` networks.
No `fetchcore-shared` backend fleet was created: DEV/BENCH reuse the existing fleet.

Images actually used: Solr 9.10.1 pinned to
`sha256:5ac29f32d689cef9722ec3b9f2c612ed11dce35d9e279ac0f7bd554ad76aa8cb`,
existing OpenSearch 3.8.0 and Qdrant v1.19.0; existing local
`fetchcore-api`, `fetchcore-mcp`, `fetchcore-web` images for serving. Source code is
mounted read-only from this checkout for the new Python serving containers.
Docker inspection artifacts retain resolved image IDs and mount/network details.

Ollama is the existing host service, reachable from Docker at
`http://host.docker.internal:11434`. It is not reachable through WSL loopback.
`embeddinggemma:latest` is already installed, digest
`85462619ee721b466c5927d109d4cb765861907d5417b9109caebc4e614679f1`.
Qdrant smoke validated provider/model identity, 768 dimensions, Projection v3,
serializer version 2 and fingerprint. No model was pulled and no Ollama was started.

## Results and evidence

| Check | Result | Evidence in operator directory |
| --- | --- | --- |
| Full Python suite | **839 PASS, 7 SKIP** (opt-in integrations) | `pytest-full.log` |
| Explicit real Solr/OpenSearch integrations | **7 PASS**, including initial/incremental/no-op lifecycle and native query semantics | `pytest-integration.log` |
| Focused infrastructure regressions | **26 PASS** | command below; included in full suite |
| Ruff, mypy, build, Compose, documentation links | **PASS**; mypy 171 source files; wheel/sdist archived | `build.log`, `build-dist/`, `compose-config.log` |
| DEV / BENCH / OPERATIONAL Solr | **PASS**: 4,428 canonical documents + snapshot each; matching corpus checksum and fingerprints | `<env>-rebuild.json`, `<env>-readiness.json` |
| Registered status / plan / sync | **PASS**: `current` / `noop` / `noop`; **0 backend writes, 0 deletes, 0 embedding calls** | `<env>-status.json`, `<env>-plan.json`, `<env>-sync.json` |
| Lexical search and bounded source | **PASS** with revision, checksum and source_ref; traversal **rejected** | `dev-search.json`, `dev-source.json`, `dev-source-denied.json` |
| Operational read-only preflight | **PASS**: `existing`, `reused`, binding `matching`; wrong repository **rejected** | `operational-preflight-final.json`, `wrong-binding.log` |
| OpenSearch / Qdrant live DEV/BENCH smoke | **PASS**: four searches, separate aliases, persisted identity; second run `reuse_only` | `backend-smoke.jsonl`, `backend-reuse.jsonl` |
| Incompatible embedding identity | **PASS**: read-only snapshot rejection, `schema_mismatch` | final record in `backend-reuse.jsonl` |
| Operational restart | **PASS**: count and hash of every document ID/checksum/version unchanged | `persistence.json` |
| Repeated setup | **PASS**: same container IDs; no duplicate containers/resources | `persistence.json`, `serving-idempotence.json`, `backend-reuse.jsonl` |
| Dedicated cleanup and reuse | **PASS**: only owned container removed; volume retained; core exists after recreation | `dedicated-cleanup.json`, `dedicated-reuse.json` |
| Configset mismatch guard | **PASS**: startup refuses incompatible configset; sentinel retained | `configset-guard.log` |
| Search Lab/API | **PASS**: health, ready, search and source; web proxy health | `api-validation.json`, `serving-check.log` |
| MCP Streamable HTTP | **PASS**: initialize, tools/list, search and get_source | `mcp-validation.json` |
| BENCH functional measurement | Executed: **5/6 expected-path hits**, zero empty responses; one quality miss retained | `bench-functional.json`, `benchmark.log` |
| Protected resources | **PASS**: DEV/OPERATIONAL document/version hashes unchanged across BENCH validation | `benchmark-protected-before.json`, `benchmark-protected-after.json` |
| Pre-existing resources | **PASS**: all original container IDs/volumes retained; legacy Solr commits and original OpenSearch document/write counts unchanged | `inventory-preservation.json`, `legacy-solr-preserved.json`, `legacy-opensearch-preserved.json` |

The three full Solr indexes have corpus checksum
`96223926bbe17e9276042d3d2db471c49a62110d47a073f780449c826a2a26f1`, indexing fingerprint
`sha256:5db61fd68556ffd80d9f0335c654525921e1a4e72e38caea777fb4a80e0bae7a` and projection fingerprint
`sha256:cfd4a63f85c8e4034bf17f0f82f08a47c717d52e75a9180ffb1eb6cad719e039`.
Snapshot counts exclude the snapshot record itself. Full initial indexing was
explicitly authorized only for these verified empty resources. Subsequent syncs
used existing compatible snapshots.

BENCH used the existing `dogfood/scenarios/fetchcore-self.yaml` (6 scenarios),
one warmup and two measured repetitions (12 observations), K=10, on the pinned
corpus. Expected-path hit rate and recall@10: **0.8333**; MRR: **0.3571**;
zero-result rate: **0**; mean candidates examined: **10**. The
`targeted-incremental-sync` scenario did not recover either expected path in the
top 10. No weights, algorithms or ground truth were changed to hide this miss.
Observed shared-host latency: p50 **1019.125 ms**, p95/p99 **1926.769 ms**.
These few repetitions on a busy shared host do not establish a performance baseline
or regression. The benchmark has no comparable before/after treatment in this task.

Some initial checks failed and were diagnosed rather than masked:
`configset_unavailable` exposed the startup defect fixed here; unpinned CLI readiness
required an explicit corpus revision; container currentness required read-only access
to the detached worktree's Git metadata. The serving override now mounts that metadata
and the exact corpus path, with Git safe-directory limited to that corpus. Initial
smoke-script assumptions about a result attribute, an error category and API intent
version were corrected; final assertions were executed successfully. No failed check
was counted as a pass without rerunning it.

Operational endpoints now available:

- Search Lab: `http://127.0.0.1:15173`
- API: `http://127.0.0.1:18765/api/health`, `/api/readiness`, `/api/search`, `/api/source`
- MCP: `http://127.0.0.1:18766/mcp`

All newly published ports bind to loopback. New serving containers are
`fetchcore-operational-api-1`, `fetchcore-operational-mcp-1`, and
`fetchcore-operational-web-1`, on the operational network. Existing shared OpenSearch
and Qdrant had broader published bindings before this work; their network configuration
was not changed.

Reproduce the automated checks from this checkout:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest -q tests/operational/test_shared_infra.py
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy src
make compose-config PYTHON=.venv/bin/python
.venv/bin/python scripts/check_docs.py
.venv/bin/python -m build

FETCHCORE_TARGETED_SOLR_URL=http://127.0.0.1:8983/solr \
FETCHCORE_TARGETED_SOLR_COLLECTION=fc-bench-integration-p0-20261016 \
FETCHCORE_INTENT_SOLR_CORE_URL=http://127.0.0.1:8983/solr/fc-bench-integration-p0-20261016 \
FETCHCORE_TARGETED_OPENSEARCH_URL=http://127.0.0.1:9200 \
FETCHCORE_INTENT_OPENSEARCH_URL=http://127.0.0.1:9200 \
.venv/bin/python -m pytest -q tests/integration
```

Integration fixtures create and clean only their unique test records/resources;
never substitute a DEV or operational core in those variables.

## Existing issues and limits

- The existing Solr service has four pre-existing failed cores: `fetchcore`,
  `fetchcore-self-dogfood`, `fetchcore-issue128-31a57474`, `fetchcore-issue128-check`.
  Their configset path under `/var/solr/data/configsets/fetchcore` lacks usable
  configuration. They were not repaired, unloaded or rebuilt. New DEV/BENCH cores
  use an additional `fetchcore-p0-v3` copy of the pinned configset. New isolated
  instances use `fetchcore` installed by the corrected startup script.
- The pre-existing Search Lab at port 5173 has healthy containers but reports
  `not_ready` against the old `fetchcore` core. Its deployment was preserved.
  The new operational Search Lab is separate, at port 15173.
- Operational incremental indexing remains Solr-only, as documented by the existing
  application boundary (#123). OpenSearch/Qdrant validation uses their existing
  adapters, with separate DEV/BENCH resources; no operational parity is claimed.
- Dedicated BENCH is available, but this host is not exclusive. No throughput/
  latency benchmark or baseline equivalence is certified.
- Old volumes, the `benchmark006_default` network, stopped unrelated containers and
  dangling images were inventoried, not declared disposable or removed.

## Daily operation on this machine

From this checkout, define paths (no common shell/system variable is repurposed):

```bash
P0_STATE=/home/operator/.local/state/fetchcore/shared-infra-p0-20261016
P0_ROOT=/home/operator/projetos/GitHub/fetchcore.worktrees/copilot
cd "$P0_ROOT"
```

Start/reuse OPERATIONAL and its optional Search Lab/MCP. These commands are
idempotent, do not index and do not recreate existing containers:

```bash
.venv/bin/python scripts/shared_infra.py up --environment operational \
  --repository oalangomes/fetchcore --backend solr
"$P0_STATE/serve.sh" up -d --no-build --no-recreate api mcp web
"$P0_STATE/serve.sh" ps
```

The new Docker serving configuration intentionally uses service DNS
`http://solr:8983/solr` inside the operational network, while the host CLI uses
`http://127.0.0.1:38983/solr`. It never uses container loopback to reach another service.

Inspect routing and actual resource inventories:

```bash
.venv/bin/python scripts/shared_infra.py env --environment dev \
  --repository oalangomes/fetchcore --shared-endpoints "$P0_STATE/shared-endpoints.json"
curl -fsS 'http://127.0.0.1:8983/solr/admin/cores?action=STATUS'
curl -fsS 'http://127.0.0.1:38983/solr/admin/cores?action=STATUS'
curl -fsS 'http://127.0.0.1:9200/_cat/aliases?format=json'
curl -fsS 'http://127.0.0.1:6333/aliases'
```

The `fc` wrapper sets the selected routing, separate registry and authorized pinned
corpus directory. It forwards the remaining arguments to this checkout's CLI:

```bash
"$P0_STATE/fc" dev doctor --json
"$P0_STATE/fc" dev readiness --json
"$P0_STATE/fc" dev search RepositoryIndexer --repo oalangomes/fetchcore --limit 3 --json
"$P0_STATE/fc" dev source repository://oalangomes/fetchcore/src/fetchcore/repository/indexing.py \
  --repo oalangomes/fetchcore --line-start 1 --line-end 12 --json
"$P0_STATE/fc" operational index status oalangomes/fetchcore --json
"$P0_STATE/fc" operational index plan oalangomes/fetchcore --json
# Only after the compatible plan, with no other writer to this core:
"$P0_STATE/fc" operational index sync oalangomes/fetchcore --json
```

For another repository/source revision, deliberately register the intended source
and inspect its plan. Do not silently repoint a pinned corpus or run `rebuild` over
an existing snapshot. Initial indexing here was explicitly run only after a missing
snapshot and zero backend documents were verified. `sync` never repaired a missing
or incompatible snapshot.

Reproduce the BENCH functional run (choose a fresh output filename if retaining prior results):

```bash
.venv/bin/python scripts/shared_infra.py run --environment bench \
  --repository oalangomes/fetchcore --shared-endpoints "$P0_STATE/shared-endpoints.json" -- \
  .venv/bin/python benchmarks/search-intent-v2/run.py \
  --workspace "$P0_STATE/corpus" --revision 4b21c08ac0cff66a5a57c657213f110e847b7b11 \
  --scenarios dogfood/scenarios/fetchcore-self.yaml \
  --output "$P0_STATE/bench-functional-next.json" \
  --label shared-infra-p0-functional --warmups 1 --repetitions 2
```

Safe restart/stop of the new operational stack (no volumes deleted):

```bash
"$P0_STATE/serve.sh" restart solr
"$P0_STATE/fc" operational readiness --json
"$P0_STATE/serve.sh" stop web api mcp solr
```

Do not stop the existing shared fleet while other consumers are using it. For a
stopped existing fleet, use its original operator configuration after inspection,
or `docker start fetchcore-solr-1 fetchcore-opensearch-1 fetchcore-qdrant-1` for those
exact existing containers; this does not recreate their volumes/configuration.

Dedicated BENCH setup and guarded cleanup:

```bash
.venv/bin/python scripts/shared_infra.py up --environment bench --dedicated \
  --repository oalangomes/fetchcore --backend solr
.venv/bin/python scripts/shared_infra.py cleanup --environment bench --dedicated \
  --repository oalangomes/fetchcore --confirm-project fetchcore-bench-dedicated
```

Cleanup removes only containers in that verified owned project; the volume and its
cores remain. Shared DEV/BENCH and OPERATIONAL cleanup requests are refused.
No global prune, volume prune or `down -v` is part of this procedure.

## Failure diagnosis and recovery

1. Check `docker ps -a`, `ss -ltn`, the exact container logs and published ports
   before starting anything. Port 8983 is intentionally reused; ports 28983/38983
   isolate dedicated/operational services. Do not kill a process to reclaim a port.
2. `doctor` checks composition/service facts; `readiness` additionally checks schema,
   snapshot and fingerprints. Healthy HTTP/container status alone is insufficient.
3. A missing service under `reuse_only` is a failure. A missing core needs explicit
   `prepare --create-if-missing`, with the correct named configset and repository.
   A missing snapshot requires a reviewed initial index; it does not trigger rebuild.
4. `configset_unavailable`: inspect the actual standalone configset directory,
   permissions and core initFailures. Preserve existing configsets; install a new
   named copy if appropriate. Do not overwrite schema to make readiness pass.
5. `schema_mismatch`, `rebuild_required`, incompatible fingerprint or corrupt snapshot:
   stop mutation and investigate the identity/configuration. Prepare a new isolated
   resource or obtain explicit approval to rebuild existing data.
6. After interruption, repeat status/plan/preflight before sync. Preserve the failed
   command's JSON and inspect repository binding; do not assume partial state is ready.

Relevant semantics are documented in the
[shared infrastructure guide](../guides/shared-development-infrastructure.md),
[registered indexing guide](../guides/repository-indexing.md),
[Docker Compose up reference](https://github.com/docker/compose/blob/main/docs/reference/compose_up.md)
and [Solr standalone configsets](https://solr.apache.org/guide/solr/latest/configuration-guide/config-sets.html).

## 2026-10-16 follow-up: multi-backend infrastructure

This follow-up records the required OpenSearch/Qdrant OPERATIONAL treatment. It
supplements the earlier evidence above; it does not replace the earlier repository,
revision or artifact identity.

### Ownership and services

The active DEV/BENCH backend services predated this run and belonged to Compose
project `fetchcore`, not `fetchcore-shared`. Their existing volumes had no
`io.fetchcore.owner` label. They were inspected and reused through the explicit
operator endpoint file; no shared container, volume, core, index or collection was
adopted, restarted, overwritten or deleted.

Created only the missing OPERATIONAL OpenSearch and Qdrant services in project
`fetchcore-operational`. Their containers and named volumes carry owner
`fetchcore-shared-infra-v1`. The existing OPERATIONAL Solr service and all
application/serving containers were retained. No dedicated BENCH OpenSearch/Qdrant
services were started; dedicated performance remains opt-in. No Ollama container
was started.

| Active backend container | Image | Published endpoint(s) | Persistent volume |
| --- | --- | --- | --- |
| `fetchcore-solr-1` (existing shared) | Solr 9.10.1, pinned digest | `127.0.0.1:8983` | `fetchcore_solr-data` |
| `fetchcore-opensearch-1` (existing shared) | OpenSearch 3.8.0 | `0.0.0.0:9200`, `0.0.0.0:9600` | `fetchcore_opensearch-data` |
| `fetchcore-qdrant-1` (existing shared) | Qdrant v1.19.0 | `0.0.0.0:6333`, `0.0.0.0:6334` | `fetchcore_qdrant-data` |
| `fetchcore-operational-solr-1` (existing isolated) | Solr 9.10.1, pinned digest | `127.0.0.1:38983` | `fetchcore-operational_solr-data` |
| `fetchcore-operational-opensearch-1` (created) | OpenSearch 3.8.0 | `127.0.0.1:39200`, metrics `127.0.0.1:39600` | `fetchcore-operational_opensearch-data` |
| `fetchcore-operational-qdrant-1` (created) | Qdrant v1.19.0 | REST `127.0.0.1:36333`, gRPC `127.0.0.1:36334` | `fetchcore-operational_qdrant-data` |
| `fetchcore-bench-dedicated-solr-1` (pre-existing opt-in) | Solr 9.10.1, pinned digest | `127.0.0.1:28983` | `fetchcore-bench-dedicated_solr-data` |

The other active `fetchcore-api`, `fetchcore-mcp`, and `fetchcore-web` containers
were left running and unchanged. The shared OpenSearch and Qdrant ports are
pre-existing wildcard-bound mappings; this change did not alter them. Newly created
OPERATIONAL backend ports bind only to loopback. No Ollama container or volume was
created for FetchCore; the unrelated existing `open-notebook_ollama_models` volume
was retained. Ollama is the existing host service.

An initial attempt to start the new OpenSearch container failed because OpenSearch
3.8's demo installer required an initial admin password even though the local
service disables its security plugin. The task-created unhealthy container was
removed by its exact name; its empty named volume was preserved. The Compose config
now sets `DISABLE_INSTALL_DEMO_CONFIG=true`, and the service subsequently reached
readiness. No existing container or persistent volume was removed.

### Namespaces and live adapter results

All resource names below are repository-scoped. DEV and BENCH aliases differ while
sharing the existing backend servers. The exact repository identity and indexed
revision remain present in retrieved provenance.

| Environment | Solr core | OpenSearch alias → index | OpenSearch docs / capability | Qdrant alias → collection | Qdrant points / vector schema |
| --- | --- | --- | --- | --- | --- |
| DEV | `fc-dev-fetchcore-635ab7352c2850f2` | `fc-dev-fetchcore-635ab7352c2850f2-635ab7352c28` → `fc-dev-fetchcore-635ab7352c2850f2-635ab7352c28-1b6579bf08df-66134bcb47fc-e3d7ea58` | 143 / lexical (no vector mapping) | `fc-dev-fetchcore-635ab7352c2850f2-635ab7352c28` → `fc-dev-fetchcore-635ab7352c2850f2-635ab7352c28-1b6579bf08-66134bcb47-8911ee81` | 143 / 768 dimensions |
| BENCH | `fc-bench-fetchcore-635ab7352c2850f2` | `fc-bench-fetchcore-635ab7352c2850f2-635ab7352c28` → `fc-bench-fetchcore-635ab7352c2850f2-635ab7352c28-1b6579bf08df-66134bcb47fc-f2c30470` | 143 / lexical (no vector mapping) | `fc-bench-fetchcore-635ab7352c2850f2-635ab7352c28` → `fc-bench-fetchcore-635ab7352c2850f2-635ab7352c28-1b6579bf08-66134bcb47-7afba6b9` | 143 / 768 dimensions |
| OPERATIONAL | `fc-operational-fetchcore-635ab7352c2850f2` | `fc-operational-fetchcore-635ab7352c2850f2-635ab7352c28` → `fc-operational-fetchcore-635ab7352c2850f2-635ab7352c28-infra-v3` | 0 / empty Projection v3 lexical schema | `fc-operational-fetchcore-635ab7352c2850f2-635ab7352c28` → `fc-operational-fetchcore-635ab7352c2850f2-635ab7352c28-infra-v3` | 0 / 768 dimensions, sparse-vector schema |

The Solr cores each contained 4,428 knowledge records. The OpenSearch DEV/BENCH
aliases and Qdrant DEV/BENCH aliases were validated read-only and reused; each
contained 143 projected records/points. OpenSearch's live existing mapping has no
vector field, so its active shared capability is lexical only. Qdrant collections
have the 768-dimensional dense vector plus the configured sparse vector; retrieval
validation used dense mode and did not require BM25/lexical retrieval.

Actual `RetrievalApplicationService` queries (`repository knowledge retrieval`,
limit 3) returned three results for each of Solr, OpenSearch and Qdrant in DEV and
BENCH. All returned results preserved `repository://oalangomes/fetchcore/...`,
revision `4b21c08ac0cff66a5a57c657213f110e847b7b11` and checksums. Solr also returned
three results from its OPERATIONAL core.

The OPERATIONAL OpenSearch index and Qdrant collection were intentionally created
empty: 0 documents/points and no repository snapshot. A real OpenSearch adapter
query reported `snapshot_missing`. The Qdrant adapter query against the empty
collection reported `response_parsing_failure` ("active Qdrant snapshot is missing
or ambiguous"); that observed error is not counted as retrieval success. No
indexing/rebuild was run on either backend.

### Acceptance matrix

| Backend | DEV | BENCH | OPERATIONAL | Readiness | Persistence | Retrieval |
| --- | --- | --- | --- | --- | --- | --- |
| Solr | **PASS** — core exists; 4,428 records | **PASS** — distinct core; 4,428 records | **PASS** — isolated core; 4,428 records | **PASS** — Solr system endpoint and cores responded | **PASS** — named volumes mounted; OPERATIONAL corpus remained present; prior report records restart verification | **PASS** — lexical adapter returned 3 results in each environment |
| OpenSearch | **PASS** — distinct alias, 143 docs, Projection v3 lexical mapping | **PASS** — distinct alias, 143 docs, Projection v3 lexical mapping | **PASS (infrastructure)** — isolated container, alias and mapping; 0 docs | **PASS** — shared cluster yellow and OPERATIONAL cluster healthy; both accepted as ready | **PASS** — OPERATIONAL alias/mapping survived restart on the same container ID and named volume; shared service was not restarted to avoid interrupting consumers | **PASS** — lexical adapter returned 3 results in DEV/BENCH; **BLOCKED** in OPERATIONAL because no snapshot (index lifecycle `unsupported_capability`, #123) |
| Qdrant | **PASS** — distinct alias, 143 points, 768 dimensions | **PASS** — distinct alias, 143 points, 768 dimensions | **PASS (infrastructure)** — isolated alias/schema; 0 points | **PASS** — `/healthz` returned ready in all environments | **PASS** — OPERATIONAL alias/schema survived restart on the same container ID and named volume; shared service was not restarted to avoid interrupting consumers | **PASS** — dense adapter returned 3 results in DEV/BENCH using Ollama; **BLOCKED** in OPERATIONAL because no snapshot (index lifecycle `unsupported_capability`, #123) |

Operational indexing capability is separate from backend retrieval:

| Backend | FetchCore operational indexing |
| --- | --- |
| Solr | **PASS** — existing lifecycle retained; no indexing writes were made in this follow-up |
| OpenSearch | **BLOCKED — `unsupported_capability`;** operational indexing lifecycle depends on #123 |
| Qdrant | **BLOCKED — `unsupported_capability`;** operational indexing lifecycle depends on #123 |

All nine backend/environment readiness probes passed. Shared DEV/BENCH containers
were not restarted because they are pre-existing shared services with active
consumers; their named volume mounts and live alias/core data were verified
read-only. The newly isolated OPERATIONAL OpenSearch and Qdrant containers were
restarted individually; the same container IDs, volumes and prepared resources
remained, and `prepare` returned `state=reused` afterward.

Two pre-existing shared-service conditions were preserved and not treated as
infrastructure successes beyond the named resources tested:

- Shared Solr `/admin/cores?action=STATUS` reported `initFailures` for the legacy
  cores `fetchcore`, `fetchcore-self-dogfood`, `fetchcore-issue128-31a57474`, and
  `fetchcore-issue128-check` because their existing `fetchcore` configset could not
  load. The routed DEV/BENCH cores were independently queryable and returned 4,428
  records each. No legacy core/configset was repaired or overwritten.
- Shared OpenSearch health was `yellow` with 19 active primary shards and 3
  unassigned shards. The service and target aliases were queryable. No shard
  allocation setting or existing index was changed.

### Ollama integration

The existing Ollama service was reached from the running Docker application
container at `http://host.docker.internal:11434`. `/api/tags` found
`embeddinggemma:latest` (digest
`85462619ee721b466c5927d109d4cb765861907d5417b9109caebc4e614679f1`); an actual
`/api/embed` probe returned 768 dimensions. Qdrant dense retrieval also succeeded
through the FetchCore adapter using that model. No model was downloaded and no
Ollama service was started.

The WSL host itself could not reach the existing service on `127.0.0.1:11434`;
`host.docker.internal` resolved but timed out from WSL. The container path is
validated; WSL-host processes need an explicitly reachable endpoint via
`--ollama-url` and must fail rather than silently falling back if none is available.

### Commands and test evidence

Compose rendering passed for all three backend files. Focused regression tests:
`33 passed` (`tests/operational/test_shared_infra.py`); Ruff passed for the changed
Python script and test. Live retrieval/readiness probes were run directly against
the FetchCore adapters and service endpoints as recorded above. No frozen benchmark
was run: no exclusive-host attestation was made, and dedicated performance remains
opt-in.

Repeat the isolated setup and inspect it without deleting persistent data:

```bash
.venv/bin/python scripts/shared_infra.py up --environment operational \
  --repository oalangomes/fetchcore --backend opensearch
.venv/bin/python scripts/shared_infra.py up --environment operational \
  --repository oalangomes/fetchcore --backend qdrant
.venv/bin/python scripts/shared_infra.py prepare --environment operational \
  --repository oalangomes/fetchcore --backend opensearch --create-if-missing
.venv/bin/python scripts/shared_infra.py prepare --environment operational \
  --repository oalangomes/fetchcore --backend qdrant --vector-dimensions 768 \
  --create-if-missing
.venv/bin/python scripts/shared_infra.py ready --environment operational \
  --repository oalangomes/fetchcore --backend opensearch
.venv/bin/python scripts/shared_infra.py ready --environment operational \
  --repository oalangomes/fetchcore --backend qdrant
docker ps --filter name=fetchcore
docker volume ls --format '{{.Name}}' | grep '^fetchcore'
```

`up` uses `--no-recreate`; resource preparation without `--create-if-missing` is
read-only. `prepare` never deletes or rebuilds an existing index/collection.
