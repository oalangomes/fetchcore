# Backend reconciliation acceptance — 2026-11-09

## Contract

The opt-in `reconciliation` policy runs only from explicit indexing commands.
The application checks a clean, exact Git source revision, classifies the active
backend and snapshot, and selects no-op, incremental sync, payload-index repair,
or rebuild. A local resource/repository lock serializes reconciliation commands.
Adapters retain physical ownership checks and validate the staged projection
before publication. Serving and retrieval never perform repair.

`inspect` makes no writes. `safe-auto` requires `managed: true` and a proven
repository binding. `explicit-rebuild` uses the same source and backend checks;
on Solr it creates a distinct staging core for each requested reconstruction.
Solr Standalone with a classic schema uses a versioned server-side configset,
stages a new core, validates it, and executes CoreAdmin `SWAP`. OpenSearch and
Qdrant use their existing writer locks, staging generations and aliases. Qdrant
reprojects changed payloads with prior vectors only after exact provider, model,
serializer, dimension and serialized-input checks. Missing payload indexes are
added in place only when stored keys match canonical evidence.

## Live Solr recovery

The dedicated operational core had 5,232 records (5,231 knowledge documents and
one snapshot) at revision `6cd089db8591f481e6488fed1bd49f2a412fbdda`.
Its loaded classic `schema.xml` lacked `structural_names`,
`structural_qualified_names`, `structural_identities`, and
`structural_incoming_identities`. The read-only plan reported these exact missing
fields, the old indexing/projection fingerprints, and `staged_rebuild`.

A new `fetchcore-structural-v3-20261109` server-side configset was installed
without replacing the old configset or volume. The source was materialized as a
clean local Git clone at `0d50d0b4a09932b876cf13867d4dd9ff77388c88`.
The configured Ollama `embeddinggemma` endpoint returned one 768-dimensional
preflight vector. With `safe-auto` and `managed: true`, reconciliation produced:

| Observation | Result |
| --- | --- |
| Action | `staged_rebuild` |
| Status | `ready`, schema compatible, snapshot validated |
| Active core records | 5,411 (5,410 knowledge + one snapshot) |
| Retained prior core records | 5,232 |
| New snapshot revision | `0d50d0b4a09932b876cf13867d4dd9ff77388c88` |
| New corpus checksum | `3bb33728014eb284e8e99d402d54f9b9440b766755baea6efa55a792e5d4bdc8` |
| New indexing fingerprint | `sha256:f5286bee5b8f785ad67298337aeaab3c60bdbfebc62d7a7dfe22a7c0f6827adb` |
| Previous core | `fc-operational-fetchcore-635ab7352c2850f2-fc-reconcile-69b81dcb570948f6` |
| Total reconciliation | 431,203 ms |
| Embedding | 5,410 vectors in 28 requests, 199,777 ms |

The second `safe-auto` execution returned `action: none`, `status: ready`.
Readiness reported all service, schema, snapshot, fingerprint, embedding,
currentness, and retrieval checks `ready`. SDK smoke observations at the new
revision: `find_symbol("RepositoryIndexer")` returned one result with partial
coverage; `get_related` returned 20 import observations; lexical `search`
returned three candidates; `get_source` returned the expected path and revision.
The partial symbol status reflects persisted extraction coverage and was not
relabeled as complete.

On a separate disposable Solr core, the live test injected a snapshot-read
failure immediately after `SWAP`. The adapter swapped the prior core back,
verified the original ready snapshot remained active, and the next
reconciliation returned no-op. The prior core was not deleted.

## Live OpenSearch and Qdrant recovery

The opt-in live integration test creates isolated repository resources on each
backend. For OpenSearch it seeds an old strict mapping without
`structural_names`, then proves `safe-auto` builds a compatible generation,
switches the alias only after validation, retains the old index, and returns
no-op on repeat. For Qdrant it seeds an old indexing fingerprint, proves every
compatible 4-dimensional deterministic vector is reused during payload
reprojection, retains the previous collection, repairs a removed keyword
payload index in place, and returns no-op on repeat. Both live tests passed.
These Qdrant vectors are test vectors, not operational `embeddinggemma` vectors.

## Validation and limits

- `.venv` lint and mypy passed; build and documentation checks passed.
- Full local suite: 1,513 passed, 12 skipped. The skipped live reconciliation
  test was run separately against Solr, OpenSearch and Qdrant; each passed.
- Live structural navigation tests against OpenSearch and Qdrant also passed.
- SolrCloud automatic cutover is blocked: CoreAdmin `SWAP` is Standalone-only.
- Unknown ownership, unavailable/dirty source, incompatible embedding identity,
  and missing compatible Solr configset block mutation explicitly.
- Solr's reconciliation lock is local to the operator host. Run Solr indexing
  from one host while reconciling; external writers do not participate in that
  lock. The adapter rechecks the active snapshot before swapping.

The normal CI operational indexing workflow remains fail-closed until an
operator enables the separate reconciliation policy. No serving deployment was
recreated as part of this validation.

## PR #240 integrity follow-up

The review of commit `7327ec3` identified four publication risks. The fixes
remain within the existing reconciliation and adapter boundaries:

| Blocker | Cause and correction | Failure evidence | Status |
| --- | --- | --- | --- |
| Solr active rebuild | The additive-field path could call `full_rebuild` on the active core. All Solr rebuilds now require a compatible versioned configset and a validated Standalone staging core; unsupported cutover blocks explicitly. | Live interruption during staging left the active core unchanged; post-SWAP failure restored the previous snapshot. | Resolved |
| OpenSearch promotion | A post-promotion validation failure could leave the alias on the new generation. Promotion now reads the persisted alias, restores the previous target under the writer lock when safe, verifies restoration, and reports `publication_indeterminate` when it cannot prove rollback. Staged and prior indices are retained. | Contract tests cover pre-promotion failure, lost promotion response, post-promotion validation failure, failed rollback, and competing alias change. A live post-promotion failure restored the old alias. | Resolved |
| Git source identity | A clean preflight did not freeze bytes read by indexing. Reconciliation now creates a temporary private Git clone at the requested commit, indexes that checkout, and verifies both source checkouts before publishing a rebuilt projection. No persistent worktree is created. | A contract test modifies the original checkout during canonical indexing and proves the alias and ready snapshot remain unchanged. | Resolved |
| Solr stage collision | The deterministic stage name could contain a retained prior core after `SWAP`. Existing stages are reused only after schema, ownership, revision, checksum, fingerprint and ready-snapshot checks. Partial stages and prior cores remain untouched; a fresh name is used when needed. | Live partial-stage interruption and complete-stage interruption are followed by a CLI resume in a new process. A second explicit rebuild preserves the earlier backup; repeated safe-auto returns no-op. | Resolved |

The CI `structural-live-backends` matrix now runs the reconciliation test against
disposable Solr, OpenSearch and Qdrant resources on every PR. For Solr, the job
creates a disposable configset with the four structural fields removed, leaving
the current configset and operational core untouched. Each backend uploads a
JUnit XML result and its test log as a `reconciliation-live-*` artifact.

Local validation of this follow-up: lint, mypy, build and documentation checks
passed; the full suite had 1,519 passed and 12 optional live tests skipped.
The opt-in integration test passed separately against all three real services.
The Solr failure sequence took 435.82 seconds because it exercised repeated
staging, a fresh CLI process, cutover rollback and an explicit second rebuild.
An application contract test also rejects structural retrieval from a published
snapshot with an incompatible projection fingerprint.

The private clone ensures that published bytes come from the requested commit.
An operator working-tree change during indexing blocks a rebuild before backend
mutation. The current serving deployment and registered source revision remain
separate; this evidence does not claim the deployed CLI/MCP route has been
switched to the recovered snapshot. SolrCloud automatic cutover remains blocked.
