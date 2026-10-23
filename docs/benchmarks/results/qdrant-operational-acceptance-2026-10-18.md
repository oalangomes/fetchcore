# Qdrant OPERATIONAL acceptance — 2026-10-18

## Scope and decision

**PASS for the Qdrant lifecycle gate.** This run exercised the real FetchCore
CLI/application boundary against Qdrant 1.19.0 and an existing Ollama service.
It proves operational lifecycle behavior for these two repository sources and
revisions. It does not compare ranking quality with a retrieval baseline.

The initial full-corpus rebuild ran from PR #192 implementation HEAD
`c73f5c3f422c02736ea0c5da103ee3e846287b6e`. The controlled incremental
run exposed two defects. They were fixed in
`8cfec1b94f1661e7617a830220ecc80d46e9036e`, and the successful
incremental/no-op operations used that commit's worktree. The acceptance source
was separate from the PR worktree and the main repository.

| Gate | Result | Evidence |
| --- | --- | --- |
| A. Bootstrap, status, plan | PASS | Projection v3 dense/sparse schema; initial snapshot absent; plan predicted 4,424 documents; no Qdrant collection/alias change during reads |
| B. Explicit rebuild | PASS | New immutable generation, 4,424 documents, 4,425 points including snapshot, checksum verified, snapshot `ready`, alias promoted; empty prior collection retained |
| C. Same-revision no-op | PASS | Plan `noop`; sync reports zero embeddings, writes, deletes, creations and promotions; alias unchanged |
| D. Committed Git incremental | PASS after fixes | Isolated namespace; add/update/delete/rename; prior generation retained; changed revision and checksum; one vector reused with identical bytes; post-sync no-op |
| E. Dense, hybrid, source | PASS | Both modes returned evidence from the indexed revision; `source` matched `git show` for the returned `source_ref` |
| F. P0 tests | PASS | Four pytest cases: interprocess writer lock, physical snapshot pin, dense and hybrid search pin during alias promotion |

## Preflight and effective configuration

The existing worktree was
`/home/operator/projetos/GitHub/fetchcore.worktrees/operational-qdrant`.
Its two pre-existing, uncommitted files contained merge markers and were saved
as a named Git stash before a fast-forward to the remote PR HEAD. They were
not discarded or included in this PR. The PR's backup branches were retained.
The reference `master` SHA was `0043e74e11dafa8e189aacf7104d7cb8277f34b6`;
the PR was ahead by 9 and behind by 0 before the new commits.

The Qdrant container was `fetchcore-operational-qdrant-1`, Compose project
`fetchcore-operational`, owner label `fetchcore-shared-infra-v1`, and volume
`fetchcore-operational_qdrant-data` mounted at `/qdrant/storage`. The volume
had matching project and owner labels. `/readyz` reported all shards ready.
The host endpoint was `http://127.0.0.1:36333`. The existing repository alias
was `fc-operational-fetchcore-635ab7352c2850f2-635ab7352c28`, pointing to
`fc-operational-fetchcore-635ab7352c2850f2-635ab7352c28-infra-v3` with
zero points, a 768-dimensional cosine dense vector, `lexical` sparse vector
with `idf`, and no repository snapshot. No local FetchCore writer process or
Qdrant writer-lock collection was observed. This local process check cannot
exclude an external writer; the Qdrant interprocess lock also guards writes.

The WSL default route was resolved dynamically to `172.21.32.1`.
`http://172.21.32.1:11434/api/tags` listed `embeddinggemma:latest`.
FetchCore's `OllamaEmbeddingProvider.embed` made a real `/api/embed` call:
768 dimensions in 5,106.3 ms, including any model warm-up. No model or service
was installed or started. The effective runtime came from explicit CLI backend
settings and environment variables; `FETCHCORE_CONFIG` was unset. The
embedding provider was `ollama`, model `embeddinggemma`, dimension 768;
the retrieval mode was hybrid with RRF. The provider timeout was raised to
180 seconds for the full-corpus execution; this is a transport setting, not
an embedding identity change. Capabilities reported dense/hybrid retrieval
and incremental/rebuild indexing, with no lexical-only or targeted lookup claim.

| Identity | Effective value |
| --- | --- |
| Main source repository/revision | `oalangomes/fetchcore` / `4b21c08ac0cff66a5a57c657213f110e847b7b11` |
| Projection version/fingerprint | `3` / `sha256:cfd4a63f85c8e4034bf17f0f82f08a47c717d52e75a9180ffb1eb6cad719e039` |
| Serializer mode/version/fingerprint | `projection-v3` / `2` / `sha256:5f859289b8b3a594e924545edb1c3ae6178b77ea22cf3ff224d43c5ec7aeb43a` |
| Main indexing fingerprint | `sha256:4a66bd379a149aab3f3ee7f59236f53a5f08ccda7c7ac7c67ddc5ea100824bfe` |
| Sparse analyzer fingerprint | `c13fcfc1b38ca0f920ebecd74edbd7530464ec52df0db1f13327c737e86e18af` |

## Main repository rebuild, no-op, and retrieval

The registered source was the clean, detached Git worktree in the operational
registry at revision `4b21c08...`. Bootstrap reported schema
`fetchcore.qdrant.projection-v3`, `initial` / `snapshot_missing`, a successful
embedding probe, and no snapshot. Status was `absent`; plan was `initial`
with 4,424 additions and no previous count. Before and after these reads,
the alias still selected the same zero-point collection.

The explicit CLI `rebuild` created
`fc-operational-fetchcore-635ab7352c2850f2-635ab7352c28-1b6579bf08-304dac8c84-3de5b1cf`,
validated it, then promoted the alias. The previous `-infra-v3` collection
remained present with zero points. The new collection has 4,425 points:
4,424 knowledge documents plus one snapshot. Snapshot status is `ready`,
revision `4b21c08...`, document count 4,424, and corpus checksum
`304dac8c84b402f0cf3495f153a547ec0c341ce2c334c841ca44aa8e3760c4ef`.

| Rebuild measurement | Observed value |
| --- | ---: |
| Wall time / adapter duration | 174.72 s / 165,527 ms |
| Document embeddings / requests | 4,424 / 139 |
| Embedding duration | 156,034 ms |
| Backend writes / deletes | 4,425 / 0 |
| Alias promotions | 1 |
| Stage: point writes / snapshot validation / promotion | 3,850 / 2,114 / 19 ms |

The subsequent status was `current`. The plan was `noop` with 4,424
unchanged documents. The sync reported zero document embeddings, zero backend
writes/deletes, zero collection creation/deletion, and zero alias promotions.
Wall time was 0.25 s; the adapter reports zero for its no-op duration, while
CLI startup, observation, and Git discovery occur outside that span. The alias
and both physical collections were unchanged.

The query `RepositoryIndexer indexes repository knowledge` returned three
dense results in 481 ms application time (0.75 s wall), and three hybrid
results in 585 ms (0.84 s wall). The dense result included
`repository://oalangomes/fetchcore/src/fetchcore/repository/document_indexing.py`
at revision `4b21c08...`, with a canonical checksum and source span.
The hybrid result included the same source with separate dense/sparse RRF
observations. Dense query embedding took 45 ms; hybrid query embedding took
15 ms, dense acquisition 105 ms, sparse acquisition 110 ms, and fusion 8 ms.
The search response reported the snapshot revision and serializer/projection
fingerprints. These are observations from one warm local run, not latency
percentiles or ranking-quality evidence.

`fetchcore source` read lines 97–110 of that returned `source_ref` using
the expected repository and revision. Its content matched
`git show 4b21c08...:src/fetchcore/repository/document_indexing.py` for
those lines. Wall time was 0.27 s. No source latency stage is instrumented.

## Isolated committed Git incremental

A separate local Git repository,
`fetchcore-acceptance/qdrant-pr192-20261018`, was created under operator
state, with its own registry and the dedicated Qdrant prefix
`fc-operational-qdrant-pr192-20261018-1eab881d06e3bf78`. The resulting
alias was
`fc-operational-qdrant-pr192-20261018-1eab881d06e3bf78-1eab881d06e3`.
Its absence and distinctness from the main alias were checked before the
explicit `shared_infra.py prepare --create-if-missing --vector-dimensions 768`.
This preparation created an empty `-infra-v3` collection. The source had a
local Git `origin` identity only; no fetch or push was performed.

Commit `018ba60223864013a1c0e6eec1b1b33fa36f5ec0` contained
`stable.py`, `modify.py`, `delete.py`, and `move_old.py` (one Python
function/document each). The explicit rebuild produced four documents, five
points including snapshot, checksum
`8878a0f4fa30f2a7b830d45712041d3102d21c412ae9b949c79e8ab9724e7a1b`,
five backend writes, four embeddings in one request, and a `ready` snapshot
in 0.61 s wall / 363 ms adapter duration.

Commit `2c6f3d976b0e78c3a39a623139f0e742c05474d0` changed
`modify.py`, removed `delete.py`, renamed `move_old.py` to
`move_new.py` with Git reporting `R100`, added `added.py`, and left
`stable.py` untouched. Plan used `safe_git_delta`, with 3 changed and
2 removed paths; it predicted 2 added, 1 updated, 2 deleted, and 1 unchanged
document. The successful sync stayed in `incremental` mode and produced
checksum
`f09f06c46d7a3a9e6afb3db4c19b7e6f4333f4c7cfac0d743bb47a7b744df943`.

| Incremental measurement | Observed value |
| --- | ---: |
| Wall time / adapter duration | 0.70 s / 446 ms |
| Final knowledge documents / physical points | 4 / 5 including snapshot |
| Document embeddings / requests / vectors reused | 3 / 1 / 1 |
| Backend writes / point deletes / promotions | 7 / 2 / 1 |
| Stage: copy / deletes / upserts / snapshot validation / promotion | 9 / 17 / 6 / 5 / 28 ms |
| Prior and active generation point counts | 5 / 5 |

The reported execution counts are 2 added, 2 updated, 2 deleted, 0
unchanged. One extra update is the provenance-only refresh of `stable.py`
from the first commit to the second; it reused its vector and was not
re-embedded. Direct read-only scrolling proved that its point ID and full
dense/sparse vector bytes were identical across the two physical generations,
while its document/provenance revision changed to the second commit.
`delete.py` and `move_old.py` are absent from the active generation;
`move_new.py` and `added.py` are present. The previous generation is
retained. The later plan and sync were `noop`; sync again reported zero
writes, deletes, embeddings, creations, and promotions.

`backend_writes=7` is instrumented. Two copied points and four document
upserts are **derived** from the adapter's accounting
(7 writes − 1 snapshot − 4 upserts = 2 copies), not separate emitted
metrics. The adapter reports point deletes directly. Neither Qdrant wire
request count nor storage bytes copied are instrumented; they are not zero.

## Failure chronology and retained resources

The first real controlled `sync` failed before staging with
`unsafe_snapshot`: the safe Git delta retained `stable.py` with the previous
revision, while Qdrant correctly refused mixed-revision projection input.
The Qdrant operational driver now refreshes revision/provenance only for
retained documents whose path was outside the verified Git change set and whose
prior canonical document is identical. A focused committed-Git test proves the
new revision and vector reuse.

A direct diagnostic call then reached staging and exposed a second defect:
`DELETE /points` returned HTTP 404. The Qdrant API requires
`POST /points/delete`. The adapter and its strict test transport now use
that endpoint. The failed stage
`fc-operational-qdrant-pr192-20261018-1eab881d06e3bf78-1eab881d06e3-e67d1ae192-f09f06c46d-9b06e338`
is retained with two points for operator inspection. It has no alias. The
active alias stayed on the prior `ready` generation until the successful
retry. No automatic unlock or collection cleanup was performed; final
inventory had no writer-lock collection.

## Reproduction and measurement limits

These are the key commands, run from the PR worktree with an explicitly
authorized local source and the existing operational service. The controlled
source used a separate `--cache-root` and Qdrant collection prefix.

```bash
FC_GATEWAY=$(ip -4 route get 1.1.1.1 | awk '{for (i=1;i<=NF;i++) if ($i=="via") {print $(i+1); exit}}')
export FETCHCORE_BACKEND=qdrant-vector
export FETCHCORE_QDRANT_URL=http://127.0.0.1:36333
export FETCHCORE_QDRANT_COLLECTION=fc-operational-fetchcore-635ab7352c2850f2
export FETCHCORE_QDRANT_VECTOR_DIMENSIONS=768
export FETCHCORE_OLLAMA_URL="http://$FC_GATEWAY:11434"
export FETCHCORE_OLLAMA_TIMEOUT_SECONDS=180
export FETCHCORE_RETRIEVAL='{"dense":{"enabled":true,"embedding":{"provider":"ollama","model":"embeddinggemma","dimensions":768}},"hybrid":{"enabled":true}}'
FC_REGISTRY=/home/operator/.local/state/fetchcore/shared-infra-p0-20261016/operational-registry
FC_SOURCE=/home/operator/.local/state/fetchcore/shared-infra-p0-20261016/corpus
FC_SETTINGS='{"url":"http://127.0.0.1:36333","collection":"fc-operational-fetchcore-635ab7352c2850f2","vector_dimensions":768}'
.venv/bin/fetchcore index --backend qdrant-vector --backend-settings "$FC_SETTINGS" --cache-root "$FC_REGISTRY" --allowed-root "$FC_SOURCE" status oalangomes/fetchcore --json
.venv/bin/fetchcore index --backend qdrant-vector --backend-settings "$FC_SETTINGS" --cache-root "$FC_REGISTRY" --allowed-root "$FC_SOURCE" plan oalangomes/fetchcore --json
# Rebuild is explicitly authorized only after inspecting ownership, alias,
# schema, snapshot, point count, and concurrent writers.
.venv/bin/fetchcore index --backend qdrant-vector --backend-settings "$FC_SETTINGS" --cache-root "$FC_REGISTRY" --allowed-root "$FC_SOURCE" rebuild oalangomes/fetchcore --json
.venv/bin/fetchcore index --backend qdrant-vector --backend-settings "$FC_SETTINGS" --cache-root "$FC_REGISTRY" --allowed-root "$FC_SOURCE" sync oalangomes/fetchcore --json
```

The controlled fixture files and raw JSON/time output remain in local operator
state at
`/home/operator/.local/state/fetchcore/pr192-qdrant-acceptance-20261018`.
The versioned measurements here are transcribed from those raw CLI and Qdrant
responses. Exact fixture Git SHAs depend on local commit metadata if recreated.
No DEV/BENCH, OpenSearch, or Solr resource was sent a write request in this run.

The main snapshot remains pinned to a historical clean source revision; this
gate does not claim that the current PR HEAD has itself been indexed. The two
physical Qdrant generations and the failed, unaliased stage remain present.
Those retained resources need explicit operator policy if later reclaimed.

## Local verification after fixes

The requested P0 cases were run explicitly with the PR source on
`PYTHONPATH=src`: 4 passed (interprocess writer lock, snapshot physical
collection pin, dense pin, hybrid pin). The full suite then passed with
**1,084 passed, 9 skipped**. Ruff passed; `mypy src` found no issues in
181 source files; `uv build --offline` produced the sdist and wheel;
`git diff --check` passed. The documentation link check reached 67 Markdown
files with valid local links. The localization check exited successfully and
reported its existing inventory counts (38 missing, 2 stale, 2 valid); no
localization files were changed for this operational evidence page. GitHub
workflow conclusions for the final published HEAD are recorded in PR #192,
because a workflow result cannot exist before that HEAD is pushed.
